# Linux same-session architecture

## Invariants

- Attach to the current user's real Wayland, Hyprland, D-Bus, and AT-SPI sessions.
- Reuse existing application processes and profiles.
- Keep the physical workspace, focused window, and pointer unchanged for normal operations.
- Prefer exact window capture and semantic accessibility actions.
- Restore all compositor state after any fallback transaction.

## Capability map

| Need | Primary route | Interference |
|---|---|---|
| List real windows | `hyprctl clients -j` | None |
| Capture one window | `grim -T <stableId>` | None |
| Click an accessible control | AT-SPI `DoAction` | None |
| Edit accessible text/value | AT-SPI EditableText/Value | None |
| Send a discrete key | `hl.dsp.send_shortcut` by window address | None |
| Coordinate click/scroll/drag (Wayland) | Hyprland target-surface injector | None observed |
| Coordinate click/scroll/drag (XWayland) | XWayland-internal XTEST pointer | None observed |
| Unsupported client fallback | Snapshot, focus, inject, restore | Possible brief contention |

## Headless transaction

Use only for a client that rejects targeted pointer injection or for an explicitly requested dedicated view:

1. Create a named Hyprland headless output.
2. Record the target window's address, workspace, monitor, fullscreen state, the current active window/workspace, and pointer coordinates.
3. Move the existing window with `follow = false`.
4. If it does not already cover the fallback screen, set compositor fullscreen mode while keeping its client fullscreen request unchanged.
5. Perform the shortest possible operation batch.
6. Restore the original fullscreen modes before returning the window to its workspace.
7. Remove the headless output.
8. Restore pointer and focus if either changed.

Run cleanup even after timeouts or errors. Never close or relaunch the leased application as cleanup.

## Honest boundary

Stock Hyprland still exposes one physical compositor seat. The targeted-pointer extension avoids that limitation by delivering an atomic event sequence directly to a selected client surface and restoring pointer focus before the next physical event is processed. XWayland uses its own internal XTEST pointer and restores it after each action. This is not a second general-purpose seat, but it provides non-interfering window-local click, scroll, and drag behavior for tested Wayland and XWayland clients.
