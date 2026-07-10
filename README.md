# Linux Computer Use Patch

Same-session computer control for Linux and Hyprland.

This project lets an automation agent inspect and operate the applications that are already running in the user's real desktop login. It preserves the same processes, profiles, signed-in sessions, files, and open windows instead of launching applications in a VM, nested compositor, or alternate home directory.

## Capabilities

- Enumerate live Hyprland windows and their workspaces.
- Capture an exact window on an inactive workspace without focusing or moving it.
- Send address-targeted keyboard shortcuts.
- Click, scroll, and drag inside background native Wayland windows without moving the physical cursor.
- Click, scroll, and drag inside background XWayland windows through XWayland's internal XTEST pointer.
- Delegate semantic controls and text editing to AT-SPI accessibility tooling.
- Fall back to a temporary headless output for focus-dependent applications.
- Fullscreen a fallback-only window over the temporary screen when necessary, then restore its original fullscreen mode, workspace, focus, and cursor.
- Recover compositor state after an interrupted fallback lease.

## Design

| Operation | Backend | Normal physical interference |
|---|---|---|
| Window discovery | `hyprctl clients -j` | None |
| Background capture | `grim -T <stableId>` | None |
| Semantic UI actions | AT-SPI | None |
| Targeted shortcuts | Hyprland address dispatcher | None |
| Native Wayland pointer actions | Hyprland target-surface extension | None observed |
| XWayland pointer actions | XTEST internal pointer with restoration | None observed |
| Compatibility fallback | Temporary headless output | Brief input contention is possible |

The native extension sends a complete event transaction directly to the selected Wayland surface, then restores pointer focus before the next compositor event. It never moves Hyprland's physical pointer. XWayland actions snapshot and restore XWayland's separate internal pointer.

The fallback reuses the same application process. It does not create another profile or login. The target may be fullscreened on the temporary output, and all recorded compositor state is restored afterward.

## Repository layout

- `hyprland/` — ABI-checked native Wayland target-pointer extension.
- `src/same_session_computer_use/` — MCP broker and transactional fallback manager.
- `skills/same-session-computer-use/` — Codex operating policy and architecture notes.
- `.codex-plugin/` and `.mcp.json` — Codex plugin metadata.
- `docs/plan.md` — implementation plan and supported boundary.

## Requirements

The implementation is Hyprland-specific. It was developed and accepted against Hyprland 0.55.4. Native extensions must be rebuilt for the exact running Hyprland ABI.

Runtime and build dependencies include:

- Hyprland and its development headers
- a C++23 compiler and `make`
- `pkg-config`
- `grim`
- `xdotool` with XTEST support
- Python 3
- an enabled AT-SPI session for semantic accessibility actions

The broker builds and loads the native extension on demand when the loaded Hyprland version changes.

## Build the native extension

```bash
make -C hyprland clean all
hyprctl plugin load "$(realpath hyprland/same-session-target-pointer.so)"
hyprctl plugin list
```

The generated shared object is intentionally excluded from Git. Build it on the target machine so it matches that machine's Hyprland ABI.

## Run the MCP broker

```bash
./bin/same-session-computer-use-mcp
```

The included Codex plugin manifest registers this broker and the included skill describes the accessibility-first control policy.

## Safety boundary

Hyprland still has one physical compositor seat. Normal window-local actions bypass that seat, but the compatibility fallback temporarily focuses the leased application and may contend with physical input. It therefore requires explicit acknowledgement, records state before acting, and supports crash recovery.

The extension refuses input while the session is locked, a physical button is held, pointer constraints are active, or drag-and-drop is in progress. It is not intended to bypass authentication surfaces, anti-cheat systems, or application security controls.
