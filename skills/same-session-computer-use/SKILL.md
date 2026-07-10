---
name: same-session-computer-use
description: Operate native applications in the user's real current Hyprland login while preserving their existing processes, profiles, signed-in sessions, open files, and application state. Use when the user asks Codex to view, screenshot, or control an already-open Linux app; to behave like macOS Computer Use; to work in the background without creating an isolated desktop; or to avoid disturbing the physical pointer, focus, and workspace.
---

# Hyprland Codex Background Computer Use

Operate the real logged-in session. Never substitute a VM, nested desktop, alternate `HOME`, fresh browser profile, duplicate application profile, or isolated D-Bus session.

## Workflow

1. Call `session_status`, then `list_session_windows`.
2. Reuse an existing matching window. Preserve its process, profile, login, open documents, workspace, and fullscreen state.
3. Capture with `capture_session_window`. This uses the window's Hyprland stable ID and does not focus, move, or raise it.
4. Inspect and act with the bundled Linux Computer Use accessibility tools. Refresh app state immediately before choosing an element.
5. Prefer semantic AT-SPI operations in this order:
   - `perform_action` for buttons, links, menu items, and other actionable controls.
   - `set_value` or editable-text operations for text fields and sliders.
   - `send_window_shortcut` for discrete keys or shortcuts that Hyprland can deliver to a window address without focus.
6. For coordinate-only UI, use `targeted_pointer_click`, `targeted_pointer_scroll`, or `targeted_pointer_drag`. These route directly to the selected Wayland surface or XWayland window and do not move the physical cursor or focus the app.
7. Capture the exact window again to verify the result.

## Non-interference rules

- Do not use coordinate clicks, pointer moves, drags, global typing, or focus-changing keyboard injection when an accessibility or targeted-key route exists.
- Do not move a real window to a headless output merely to capture it; exact capture works on inactive workspaces.
- Do not launch another instance of an app when a usable existing instance is present.
- Treat window addresses and capture IDs as ephemeral. Refresh them before each operation batch.
- Never hardcode AT-SPI bus names or object paths. Discover the current accessibility tree and match controls by role, name, and supported action.
- Stop if a requested action would overwrite unsaved work, close an app, sign out, or otherwise cause data loss without the user's explicit authorization.

## Targeted pointer rules

The same-session broker provides window-local pointer injection without moving Hyprland's physical cursor:

1. Refresh `list_session_windows` immediately before acting and use the current address.
2. Use coordinates from the latest exact `capture_session_window` image.
3. Keep every coordinate inside the returned window dimensions.
4. Prefer `targeted_pointer_click` over the headless lease fallback.
5. Verify the result with another exact capture.

Native Wayland events are delivered atomically by a version-matched Hyprland plugin. XWayland events are delivered through XWayland's internal XTEST pointer, which is distinct from Hyprland's physical cursor position.

## Headless output

A temporary Hyprland headless output is now an emergency compatibility fallback only. Use it when a client rejects both semantic accessibility actions and targeted pointer injection. Follow [architecture.md](references/architecture.md), obtain explicit interference acknowledgment, and leave `fullscreen_if_needed` enabled unless the task specifically requires the window's original fallback geometry. The broker fullscreens the target over the temporary screen and records its previous state. When using global fallback input, translate screenshot-local coordinates with the `coordinate_space` origin and scale returned by `capture_coordinate_desktop`. Always restore leased windows, focus, workspace, fullscreen state, and pointer position in a `finally`-style cleanup.
