from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
SERVER_INFO = {"name": "same-session-computer-use", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-11-25"
STATE_DIR = Path.home() / ".local/state/same-session-computer-use"
LEASE_FILE = STATE_DIR / "coordinate-lease.json"
LOCK_FILE = STATE_DIR / "coordinate-lease.lock"
POINTER_LOCK = threading.Lock()
_SESSION_ATTACHED = False

TOOLS = [
    {
        "name": "session_status",
        "description": "Check whether the real logged-in Hyprland session supports exact background window capture and targeted shortcuts.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "list_session_windows",
        "description": "List real windows from the user's current Hyprland login, including workspace, process, accessibility hints, and exact-capture identifiers.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "capture_session_window",
        "description": "Capture one exact real window without focusing it, moving it, changing workspace, or moving the pointer. Identify it by Hyprland address, exact-capture identifier, class, or title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "Hyprland address, exact-capture identifier, exact class, or title substring."},
                "save_path": {"type": ["string", "null"], "description": "Optional absolute PNG path to atomically create or replace after capture succeeds."},
            },
            "required": ["window"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "send_window_shortcut",
        "description": "Send a key or shortcut directly to one real window by Hyprland address without focusing it. Prefer accessibility actions for buttons and text fields; use this for discrete shortcuts or characters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Exact Hyprland window address from list_session_windows."},
                "key": {"type": "string", "description": "Hyprland key name, such as x, SPACE, RETURN, or XF86AudioPlay."},
                "modifiers": {"type": "string", "description": "Space-separated modifiers, such as CTRL SHIFT; empty for none.", "default": ""},
            },
            "required": ["address", "key"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "targeted_pointer_click",
        "description": "Click an exact coordinate inside a real Wayland or XWayland window without moving the physical cursor, changing the user's focused window, or switching workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
            },
            "required": ["window", "x", "y"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "targeted_pointer_scroll",
        "description": "Scroll at an exact coordinate inside a real Wayland or XWayland window without moving the physical cursor, changing focus, or switching workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {"window": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "steps": {"type": "integer", "minimum": -20, "maximum": 20}},
            "required": ["window", "x", "y", "steps"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "targeted_pointer_drag",
        "description": "Drag between exact coordinates inside a real Wayland or XWayland window without moving the physical cursor, changing focus, or switching workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string"}, "start_x": {"type": "number"}, "start_y": {"type": "number"}, "end_x": {"type": "number"}, "end_y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"}, "motion_steps": {"type": "integer", "minimum": 2, "maximum": 32, "default": 8},
            },
            "required": ["window", "start_x", "start_y", "end_x", "end_y"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "begin_coordinate_lease",
        "description": "Move one existing real window to a temporary off-screen Hyprland output for coordinate-only Computer Use. Fullscreens the window on that fallback screen when needed, then restores its original fullscreen state, placement, physical focus, workspace, and pointer. This briefly takes global input focus and requires explicit acknowledgment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "Window address, capture ID, exact class, or title substring."},
                "acknowledge_interference": {"type": "boolean", "description": "Must be true; raw pointer input can briefly contend with the user's physical input."},
                "fullscreen_if_needed": {"type": "boolean", "description": "Fullscreen the target on the temporary screen when it is not already fullscreen. Defaults to true.", "default": True},
            },
            "required": ["window", "acknowledge_interference"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "capture_coordinate_desktop",
        "description": "Capture the temporary off-screen output for an active coordinate lease.",
        "inputSchema": {"type": "object", "properties": {"lease_token": {"type": "string"}}, "required": ["lease_token"]},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "end_coordinate_lease",
        "description": "Restore the leased real window's original fullscreen mode, workspace, physical focus, and pointer, then remove the temporary output.",
        "inputSchema": {"type": "object", "properties": {"lease_token": {"type": "string"}}, "required": ["lease_token"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "recover_coordinate_lease",
        "description": "Recover and restore compositor state from any unfinished coordinate lease after an interruption or crash.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


def run(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def find_xwayland_display(instance: str, wayland_display: str, proc_root: Path = Path("/proc")) -> str | None:
    candidates: list[tuple[int, str]] = []
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid() or (process / "comm").read_text().strip() != "Xwayland":
                continue
            entries = (process / "environ").read_bytes().split(b"\0")
            environment = {
                key.decode(): value.decode()
                for entry in entries if b"=" in entry
                for key, value in [entry.split(b"=", 1)]
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeDecodeError):
            continue
        if environment.get("HYPRLAND_INSTANCE_SIGNATURE") != instance:
            continue
        if environment.get("WAYLAND_DISPLAY") != wayland_display:
            continue
        display = environment.get("DISPLAY")
        if display:
            candidates.append((int(process.name), display))
    return max(candidates)[1] if candidates else None


def ensure_session_environment() -> None:
    global _SESSION_ATTACHED
    if _SESSION_ATTACHED:
        return
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        proc = subprocess.run(["hyprctl", "instances", "-j"], text=True, capture_output=True, timeout=5, check=False)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "failed to discover the active Hyprland session")
        instances = []
        for instance in json.loads(proc.stdout):
            process = Path(f"/proc/{instance.get('pid')}")
            if process.exists() and process.stat().st_uid == os.getuid():
                instances.append(instance)
        if not instances:
            raise RuntimeError("no live Hyprland session belongs to this login")
        wayland_display = os.environ.get("WAYLAND_DISPLAY")
        matching = [instance for instance in instances if instance.get("wl_socket") == wayland_display] if wayland_display else []
        selected = max(matching or instances, key=lambda instance: int(instance.get("time") or 0))
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = str(selected["instance"])
        os.environ.setdefault("WAYLAND_DISPLAY", str(selected["wl_socket"]))
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    xwayland_display = (
        find_xwayland_display(os.environ["HYPRLAND_INSTANCE_SIGNATURE"], wayland_display)
        if wayland_display else None
    )
    if xwayland_display:
        os.environ["DISPLAY"] = xwayland_display
    elif not os.environ.get("DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X*"))
        if len(sockets) == 1 and sockets[0].name[1:].isdigit():
            os.environ["DISPLAY"] = f":{sockets[0].name[1:]}"
    _SESSION_ATTACHED = True


def hypr_windows() -> list[dict[str, Any]]:
    ensure_session_environment()
    proc = run(["hyprctl", "clients", "-j"])
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "failed to enumerate Hyprland windows")
    return json.loads(proc.stdout)


def combine_windows() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for win in hypr_windows():
        title = str(win.get("title") or "")
        klass = str(win.get("class") or "")
        workspace = win.get("workspace") or {}
        result.append({
            "address": win.get("address"),
            "class": klass,
            "title": title,
            "pid": win.get("pid"),
            "workspace": workspace.get("id"),
            "workspace_name": workspace.get("name"),
            "monitor": win.get("monitor"),
            "focused": win.get("focusHistoryID") == 0,
            "mapped": win.get("mapped", True),
            "fullscreen": win.get("fullscreen", 0),
            "fullscreen_client": win.get("fullscreenClient", 0),
            "floating": win.get("floating", False),
            "xwayland": win.get("xwayland", False),
            "at": win.get("at"),
            "size": win.get("size"),
            "capture_id": str(win.get("stableId")) if win.get("stableId") is not None else None,
        })
    return result


def resolve_window(query: str) -> dict[str, Any]:
    windows = combine_windows()
    q = query.lower()
    exact = [w for w in windows if q in {str(w.get("address") or "").lower(), str(w.get("capture_id") or "").lower(), str(w.get("class") or "").lower()}]
    matches = exact or [w for w in windows if q in str(w.get("title") or "").lower()]
    if not matches:
        raise RuntimeError(f"no real session window matches {query!r}")
    if len(matches) > 1:
        choices = ", ".join(f"{w.get('class')} {w.get('title')} ({w.get('address')})" for w in matches[:8])
        raise RuntimeError(f"window query is ambiguous; use an address or identifier: {choices}")
    if not matches[0].get("capture_id"):
        raise RuntimeError("the selected window has no exact-capture identifier")
    return matches[0]


def hypr_json(args: list[str]) -> Any:
    ensure_session_environment()
    proc = run(["hyprctl", "-j", *args])
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"hyprctl {' '.join(args)} failed")
    return json.loads(proc.stdout)


def hypr_dispatch(expression: str) -> None:
    ensure_session_environment()
    proc = run(["hyprctl", "dispatch", expression])
    if proc.returncode or "ok" not in proc.stdout.lower():
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"Hyprland dispatch failed: {expression}")


def load_lease() -> dict[str, Any] | None:
    if not LEASE_FILE.exists(): return None
    return json.loads(LEASE_FILE.read_text())


def save_lease(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = LEASE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(LEASE_FILE)


@contextmanager
def lease_guard():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)


def wait_for_monitor(name: str, *, present: bool, timeout: float = 5.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        monitor = next((m for m in hypr_json(["monitors"]) if m.get("name") == name), None)
        if (monitor is not None) == present: return monitor
        time.sleep(0.05)
    raise RuntimeError(f"temporary output {name} did not become {'ready' if present else 'removed'}")


def wait_for_window_fullscreen(address: str, mode: int, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window = next((w for w in combine_windows() if w.get("address") == address), None)
        if window is None:
            raise RuntimeError("leased window closed while changing fullscreen state")
        if int(window.get("fullscreen") or 0) == mode:
            return window
        time.sleep(0.05)
    raise RuntimeError(f"leased window did not reach fullscreen mode {mode}")


def begin_lease(arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments.get("acknowledge_interference") is not True:
        raise ValueError("acknowledge_interference must be true before using globally shared coordinate input")
    if load_lease():
        raise RuntimeError("a coordinate lease is already active; end or recover it first")
    selected = resolve_window(str(arguments["window"]))
    fullscreen_if_needed = arguments.get("fullscreen_if_needed", True)
    if not isinstance(fullscreen_if_needed, bool):
        raise ValueError("fullscreen_if_needed must be a boolean")
    if not selected.get("address"):
        raise RuntimeError("selected window has no Hyprland address")
    active = hypr_json(["activewindow"])
    active_workspace = hypr_json(["activeworkspace"])
    cursor = hypr_json(["cursorpos"])
    token = secrets.token_urlsafe(18)
    output = f"CODEX-CU-{token[:8]}"
    state = {
        "version": 2,
        "token": token,
        "phase": "creating",
        "output": output,
        "target": selected,
        "original": {
            "active_address": active.get("address"),
            "active_workspace": (active_workspace or {}).get("id"),
            "cursor": {"x": cursor.get("x"), "y": cursor.get("y")},
            "target_workspace": selected.get("workspace"),
            "target_monitor": selected.get("monitor"),
            "target_fullscreen": int(selected.get("fullscreen") or 0),
            "target_fullscreen_client": int(selected.get("fullscreen_client") or 0),
        },
        "fallback": {
            "fullscreen_if_needed": fullscreen_if_needed,
            "fullscreen_applied": False,
        },
    }
    save_lease(state)
    try:
        proc = run(["hyprctl", "output", "create", "headless", output])
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "failed to create temporary output")
        monitor = wait_for_monitor(output, present=True)
        workspace = int((monitor.get("activeWorkspace") or {}).get("id"))
        state["phase"] = "output-ready"; state["lease_workspace"] = workspace; save_lease(state)
        address = str(selected["address"])
        hypr_dispatch(f"hl.dsp.window.move({{ workspace = {workspace}, window = 'address:{address}', follow = false }})")
        state["phase"] = "window-moved"; save_lease(state)
        hypr_dispatch(f"hl.dsp.focus({{ window = 'address:{address}' }})")
        state["phase"] = "window-focused"; save_lease(state)
        if fullscreen_if_needed and int(selected.get("fullscreen") or 0) != 2:
            state["fallback"]["fullscreen_applied"] = True
            state["phase"] = "fullscreening"; save_lease(state)
            hypr_dispatch("hl.dsp.window.fullscreen_state({ internal = 2, client = 0 })")
            wait_for_window_fullscreen(address, 2)
        state["phase"] = "active"; save_lease(state)
        current = next((w for w in combine_windows() if w.get("address") == address), None)
        return {
            "lease_token": token,
            "output": output,
            "workspace": workspace,
            "window": current,
            "fullscreened_on_fallback_screen": bool(state["fallback"]["fullscreen_applied"]),
            "original_fullscreen": int(selected.get("fullscreen") or 0),
            "interference_boundary": "global keyboard focus and raw pointer are shared until end_coordinate_lease",
        }
    except Exception:
        try: restore_lease(state)
        except Exception: pass
        raise


def require_lease(token: str) -> dict[str, Any]:
    state = load_lease()
    if not state: raise RuntimeError("no coordinate lease is active")
    if state.get("token") != token: raise ValueError("lease token does not match the active coordinate lease")
    return state


def restore_lease(state: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    address = str((state.get("target") or {}).get("address") or "")
    original = state.get("original") or {}
    if address and any(w.get("address") == address for w in hypr_windows()):
        try:
            fullscreen = int(original.get("target_fullscreen") or 0)
            fullscreen_client = int(original.get("target_fullscreen_client") or 0)
            hypr_dispatch(f"hl.dsp.focus({{ window = 'address:{address}' }})")
            hypr_dispatch(f"hl.dsp.window.fullscreen_state({{ internal = {fullscreen}, client = {fullscreen_client} }})")
            wait_for_window_fullscreen(address, fullscreen)
        except Exception as exc: errors.append(f"fullscreen restore: {exc}")
        try:
            workspace = int(original["target_workspace"])
            hypr_dispatch(f"hl.dsp.window.move({{ workspace = {workspace}, window = 'address:{address}', follow = false }})")
        except Exception as exc: errors.append(f"window restore: {exc}")
    output = str(state.get("output") or "")
    if output and any(m.get("name") == output for m in hypr_json(["monitors"])):
        proc = run(["hyprctl", "output", "remove", output])
        if proc.returncode: errors.append(proc.stderr.strip() or proc.stdout.strip() or "output removal failed")
        else:
            try: wait_for_monitor(output, present=False)
            except Exception as exc: errors.append(str(exc))
    cursor = original.get("cursor") or {}
    if cursor.get("x") is not None and cursor.get("y") is not None:
        try: hypr_dispatch(f"hl.dsp.cursor.move({{ x = {int(cursor['x'])}, y = {int(cursor['y'])} }})")
        except Exception as exc: errors.append(f"pointer restore: {exc}")
    active_address = str(original.get("active_address") or "")
    if active_address and any(w.get("address") == active_address for w in hypr_windows()):
        try: hypr_dispatch(f"hl.dsp.focus({{ window = 'address:{active_address}' }})")
        except Exception as exc: errors.append(f"focus restore: {exc}")
    if not errors: LEASE_FILE.unlink(missing_ok=True)
    return {
        "restored": not errors,
        "errors": errors,
        "window_address": address,
        "focus_address": active_address,
        "pointer": cursor,
        "output_removed": output,
        "fullscreen_restored": int(original.get("target_fullscreen") or 0),
    }


def capture_lease(token: str) -> dict[str, Any]:
    state = require_lease(token)
    fd, name = tempfile.mkstemp(prefix="same-session-coordinate-", suffix=".png")
    os.close(fd); output = Path(name)
    proc = run(["grim", "-o", str(state["output"]), str(output)], timeout=20)
    if proc.returncode:
        output.unlink(missing_ok=True)
        raise RuntimeError(proc.stderr.strip() or "coordinate desktop capture failed")
    raw = output.read_bytes()
    data = base64.b64encode(raw).decode("ascii"); output.unlink(missing_ok=True)
    monitor = next((m for m in hypr_json(["monitors"]) if m.get("name") == state["output"]), None)
    if monitor is None:
        raise RuntimeError("coordinate lease output disappeared before capture metadata was collected")
    scale = float(monitor.get("scale") or 1)
    coordinate_width = int(round(float(monitor["width"]) / scale))
    coordinate_height = int(round(float(monitor["height"]) / scale))
    address = str((state.get("target") or {}).get("address") or "")
    current = next((w for w in combine_windows() if w.get("address") == address), state["target"])
    pixel_size = png_pixel_size(raw)
    metadata = {
        "lease_token": token,
        "output": state["output"],
        "window": current,
        "fullscreened_on_fallback_screen": bool((state.get("fallback") or {}).get("fullscreen_applied")),
        "coordinate_space": {
            "desktop_origin": {"x": int(monitor.get("x") or 0), "y": int(monitor.get("y") or 0)},
            "width": coordinate_width,
            "height": coordinate_height,
            "scale": scale,
            "screenshot_pixels": pixel_size,
            "note": "For global fallback input: desktop_x = origin.x + screenshot_x * width / screenshot_pixels.width, and likewise for y.",
        },
    }
    return {"content": [{"type": "text", "text": json.dumps(metadata, indent=2)}, {"type": "image", "data": data, "mimeType": "image/png"}], "structuredContent": metadata, "isError": False}


def physical_snapshot() -> dict[str, Any]:
    return {"active_address": hypr_json(["activewindow"]).get("address"), "workspace": hypr_json(["activeworkspace"]).get("id"), "cursor": hypr_json(["cursorpos"])}


def validate_point(window: dict[str, Any], x: float, y: float) -> None:
    size = window.get("size") or []
    if len(size) != 2 or not (0 <= x < float(size[0]) and 0 <= y < float(size[1])):
        raise ValueError(f"coordinate ({x},{y}) is outside window size {size}")


def png_pixel_size(raw: bytes) -> dict[str, int] | None:
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return {"width": int.from_bytes(raw[16:20], "big"), "height": int.from_bytes(raw[20:24], "big")}


def window_coordinate_space(window: dict[str, Any], raw: bytes) -> dict[str, Any] | None:
    size = window.get("size") or []
    pixels = png_pixel_size(raw)
    if len(size) != 2 or not pixels or not pixels["width"] or not pixels["height"]:
        return None
    width, height = float(size[0]), float(size[1])
    return {
        "window_local": {"width": width, "height": height},
        "screenshot_pixels": pixels,
        "pixel_to_window_scale": {"x": width / pixels["width"], "y": height / pixels["height"]},
        "note": "Pointer tools use window-local coordinates: multiply screenshot x/y by pixel_to_window_scale.",
    }


def ensure_target_pointer_plugin() -> None:
    plugin_name = "same-session-target-pointer"
    listed = run(["hyprctl", "plugin", "list"])
    if listed.returncode == 0 and plugin_name in listed.stdout: return
    directory = ROOT / "hyprland"
    library = directory / "same-session-target-pointer.so"
    stamp = directory / ".built-for-hyprland"
    version = run(["hyprctl", "version"])
    if version.returncode: raise RuntimeError(version.stderr.strip() or "failed to read Hyprland version")
    source_newer = not library.exists() or (directory / "target-pointer.cpp").stat().st_mtime > library.stat().st_mtime
    if source_newer or not stamp.exists() or stamp.read_text() != version.stdout:
        built = subprocess.run(["make", "clean", "all"], cwd=directory, text=True, capture_output=True, timeout=60, check=False)
        if built.returncode: raise RuntimeError(built.stderr.strip() or built.stdout.strip() or "failed to build targeted-pointer plugin")
        stamp.write_text(version.stdout)
    loaded = run(["hyprctl", "plugin", "load", str(library)], timeout=20)
    if loaded.returncode or "ok" not in loaded.stdout.lower():
        raise RuntimeError(loaded.stderr.strip() or loaded.stdout.strip() or "failed to load targeted-pointer plugin")


def resolve_xwindow_id(window: dict[str, Any]) -> str:
    pid = window.get("pid")
    if not pid: raise RuntimeError("XWayland window has no process ID")
    found = run(["xdotool", "search", "--pid", str(pid)])
    ids = [line.strip() for line in found.stdout.splitlines() if line.strip().isdigit()]
    if not ids: raise RuntimeError("xdotool could not resolve the XWayland window")
    title = str(window.get("title") or "")
    exact: list[str] = []
    for xid in ids:
        name = run(["xdotool", "getwindowname", xid])
        if name.returncode == 0 and name.stdout.strip() == title: exact.append(xid)
    if len(exact) == 1: return exact[0]
    if len(ids) == 1: return ids[0]
    raise RuntimeError("XWayland process owns multiple windows; use a unique current title")


def x_pointer_position() -> tuple[int, int]:
    proc = run(["xdotool", "getmouselocation", "--shell"])
    if proc.returncode: raise RuntimeError(proc.stderr.strip() or "failed to snapshot XWayland pointer")
    values = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return int(values["X"]), int(values["Y"])


def xdotool_target(window: dict[str, Any], command: list[str]) -> dict[str, Any]:
    xid = resolve_xwindow_id(window)
    old_x, old_y = x_pointer_position()
    try:
        proc = run(["xdotool", *command], timeout=20)
        if proc.returncode: raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "XWayland targeted input failed")
    finally:
        run(["xdotool", "mousemove", str(old_x), str(old_y)])
    return {"backend": "xwayland-xtest", "xwindow_id": xid}


def _targeted_pointer(arguments: dict[str, Any], action: str) -> dict[str, Any]:
    window = resolve_window(str(arguments["window"]))
    before = physical_snapshot()
    button = str(arguments.get("button") or "left")
    button_number = {"left": "1", "middle": "2", "right": "3"}.get(button)
    if not button_number: raise ValueError("button must be left, right, or middle")

    if action == "click":
        x, y = float(arguments["x"]), float(arguments["y"]); validate_point(window, x, y)
        count = int(arguments.get("count", 1))
        if not 1 <= count <= 3: raise ValueError("count must be between 1 and 3")
        if window.get("xwayland"):
            xid = resolve_xwindow_id(window)
            result = xdotool_target(window, ["mousemove", "--window", xid, str(round(x)), str(round(y)), "click", "--repeat", str(count), "--delay", "40", button_number])
        else:
            ensure_target_pointer_plugin()
            proc = run(["hyprctl", "-j", "cutarget", "click", str(window["address"]), str(x), str(y), button, str(count)])
            if proc.returncode: raise RuntimeError(proc.stderr.strip() or "Wayland targeted click failed")
            result = json.loads(proc.stdout)
    elif action == "scroll":
        x, y = float(arguments["x"]), float(arguments["y"]); validate_point(window, x, y)
        steps = int(arguments["steps"])
        if steps == 0 or abs(steps) > 20: raise ValueError("steps must be between -20 and 20, excluding zero")
        if window.get("xwayland"):
            xid = resolve_xwindow_id(window); wheel = "5" if steps > 0 else "4"
            result = xdotool_target(window, ["mousemove", "--window", xid, str(round(x)), str(round(y)), "click", "--repeat", str(abs(steps)), "--delay", "20", wheel])
        else:
            ensure_target_pointer_plugin()
            proc = run(["hyprctl", "-j", "cutarget", "scroll", str(window["address"]), str(x), str(y), str(steps)])
            if proc.returncode: raise RuntimeError(proc.stderr.strip() or "Wayland targeted scroll failed")
            result = json.loads(proc.stdout)
    else:
        sx, sy, ex, ey = map(float, (arguments["start_x"], arguments["start_y"], arguments["end_x"], arguments["end_y"]))
        validate_point(window, sx, sy); validate_point(window, ex, ey)
        motion_steps = int(arguments.get("motion_steps", 8))
        if not 2 <= motion_steps <= 32: raise ValueError("motion_steps must be between 2 and 32")
        if window.get("xwayland"):
            xid = resolve_xwindow_id(window)
            result = xdotool_target(window, ["mousemove", "--window", xid, str(round(sx)), str(round(sy)), "mousedown", button_number, "mousemove", "--window", xid, str(round(ex)), str(round(ey)), "mouseup", button_number])
        else:
            ensure_target_pointer_plugin()
            proc = run(["hyprctl", "-j", "cutarget", "drag", str(window["address"]), str(sx), str(sy), str(ex), str(ey), button, str(motion_steps)])
            if proc.returncode: raise RuntimeError(proc.stderr.strip() or "Wayland targeted drag failed")
            result = json.loads(proc.stdout)

    if isinstance(result, dict) and result.get("ok") is False: raise RuntimeError(str(result.get("error") or "targeted pointer action failed"))
    after = physical_snapshot()
    unchanged = after == before
    return {"action": action, "window": window, "result": result, "observed_physical_state_unchanged": unchanged, "physical_state_before": before, "physical_state_after": after, "cursor_moved_by_backend": False, "keyboard_focus_changed_by_backend": False, "workspace_changed_by_backend": False}


def targeted_pointer(arguments: dict[str, Any], action: str) -> dict[str, Any]:
    # Pointer focus and the XWayland-internal pointer are process-global resources.
    # Serialize transactions so simultaneous calls cannot interleave snapshot,
    # injection, and restoration.
    with POINTER_LOCK:
        return _targeted_pointer(arguments, action)


def text_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, indent=2, ensure_ascii=False)}], "structuredContent": value, "isError": False}


def capture_result(arguments: dict[str, Any]) -> dict[str, Any]:
    selected = resolve_window(str(arguments["window"]))
    requested_path = arguments.get("save_path")
    if requested_path:
        output = Path(str(requested_path)).expanduser()
        if not output.is_absolute():
            raise ValueError("save_path must be absolute")
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    else:
        fd, name = tempfile.mkstemp(prefix="same-session-window-", suffix=".png")
    os.close(fd)
    capture = Path(name)
    try:
        proc = run(["grim", "-T", str(selected["capture_id"]), str(capture)], timeout=20)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "exact window capture failed")
        raw = capture.read_bytes()
        data = base64.b64encode(raw).decode("ascii")
        if requested_path:
            capture.replace(output)
    finally:
        capture.unlink(missing_ok=True)
    metadata = {
        "window": selected,
        "coordinate_space": window_coordinate_space(selected, raw),
        "saved_to": str(output) if requested_path else None,
        "focus_changed": False,
        "pointer_moved": False,
        "workspace_changed": False,
    }
    return {
        "content": [
            {"type": "text", "text": json.dumps(metadata, indent=2, ensure_ascii=False)},
            {"type": "image", "data": data, "mimeType": "image/png"},
        ],
        "structuredContent": metadata,
        "isError": False,
    }


def status() -> dict[str, Any]:
    checks = {}
    for binary in ("hyprctl", "grim", "xdotool"):
        checks[binary] = subprocess.run(["sh", "-lc", f"command -v {binary}"], text=True, capture_output=True).returncode == 0
    exact_count = sum(1 for window in combine_windows() if window.get("capture_id")) if checks["hyprctl"] and checks["grim"] else 0
    plugin_loaded = False
    if checks["hyprctl"]:
        plugins = run(["hyprctl", "plugin", "list"])
        plugin_loaded = plugins.returncode == 0 and "same-session-target-pointer" in plugins.stdout
    return {
        "session": "real-current-login",
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "hyprland_instance": bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")),
        "capabilities": {
            "exact_background_window_capture": exact_count > 0,
            "targeted_background_shortcuts": checks["hyprctl"],
            "background_semantic_actions": "use the bundled Computer Use AT-SPI tools",
            "targeted_wayland_pointer": plugin_loaded,
            "targeted_xwayland_pointer": checks["xdotool"],
            "physical_pointer_seat_is_independent": False,
        },
        "checks": checks,
        "exact_window_count": exact_count,
        "raw_pointer_note": "Hyprland still has one physical pointer seat, but normal coordinate actions bypass it by targeting a Wayland surface or XWayland's internal pointer. The physical cursor, keyboard focus, and workspace are preserved.",
    }


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "session_status": return text_result(status())
    if name == "list_session_windows": return text_result({"windows": combine_windows()})
    if name == "capture_session_window": return capture_result(arguments)
    if name == "send_window_shortcut":
        address = str(arguments["address"])
        if not address.startswith("0x") or not any(w.get("address") == address for w in hypr_windows()):
            raise ValueError("address must be a live Hyprland window address from list_session_windows")
        key = str(arguments["key"])
        modifiers = str(arguments.get("modifiers") or "")
        if not re.fullmatch(r"[A-Za-z0-9_+\-]+", key):
            raise ValueError("key must be a Hyprland key name containing only letters, digits, underscore, plus, or hyphen")
        if not re.fullmatch(r"[A-Za-z ]*", modifiers):
            raise ValueError("modifiers may contain only modifier names and spaces")
        proc = run(["hyprctl", "dispatch", f"hl.dsp.send_shortcut({{ mods = '{modifiers}', key = '{key}', window = 'address:{address}' }})"])
        if proc.returncode or "ok" not in proc.stdout.lower():
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "targeted shortcut failed")
        return text_result({"sent": True, "address": address, "key": key, "modifiers": modifiers, "focus_changed": False, "pointer_moved": False})
    if name == "targeted_pointer_click": return text_result(targeted_pointer(arguments, "click"))
    if name == "targeted_pointer_scroll": return text_result(targeted_pointer(arguments, "scroll"))
    if name == "targeted_pointer_drag": return text_result(targeted_pointer(arguments, "drag"))
    if name == "begin_coordinate_lease":
        with lease_guard(): return text_result(begin_lease(arguments))
    if name == "capture_coordinate_desktop":
        with lease_guard(): return capture_lease(str(arguments["lease_token"]))
    if name == "end_coordinate_lease":
        with lease_guard(): return text_result(restore_lease(require_lease(str(arguments["lease_token"]))))
    if name == "recover_coordinate_lease":
        with lease_guard():
            state = load_lease()
            return text_result({"restored": True, "message": "no unfinished coordinate lease"} if not state else restore_lease(state))
    raise ValueError(f"unknown tool: {name}")


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    if request_id is None: return None
    method = message.get("method")
    try:
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION)
            result = {"protocolVersion": requested, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO, "instructions": "Operate the user's real logged-in Hyprland session. Capture exact windows here; use bundled Computer Use AT-SPI actions before any raw pointer fallback."}
        elif method == "tools/list": result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            args = params.get("arguments") or {}
            if not isinstance(args, dict): raise ValueError("tool arguments must be an object")
            result = call_tool(str(params.get("name") or ""), args)
        elif method == "ping": result = {}
        else: return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}


def main() -> int:
    lock = threading.Lock()
    workers: list[threading.Thread] = []
    def process(message: dict[str, Any]) -> None:
        response = dispatch(message)
        if response is not None:
            with lock:
                print(json.dumps(response, separators=(",", ":")), flush=True)
    for line in sys.stdin:
        try: message = json.loads(line)
        except Exception as exc:
            with lock: print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}), flush=True)
            continue
        worker = threading.Thread(target=process, args=(message,), daemon=True)
        workers.append(worker); worker.start()
    for worker in workers: worker.join(timeout=30)
    return 0
