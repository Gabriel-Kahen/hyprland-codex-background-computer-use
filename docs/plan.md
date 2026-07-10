# Same-session Linux Computer Use plan

## Goal

Give Codex macOS-like Computer Use behavior on Linux by operating the exact applications in the current login: the same processes, profiles, signed-in sessions, files, and open state.

## Implemented behavior

- Discover real Hyprland windows, processes, workspaces, addresses, and stable capture IDs.
- Capture exact application windows without focusing, raising, or moving them.
- Use AT-SPI for semantic actions, editable text, and values.
- Send discrete keys and shortcuts directly to a window address.
- Click, scroll, and drag inside native Wayland and XWayland windows without moving the physical cursor or changing the user's focused workspace.
- Use a recoverable temporary headless output for clients that require real focus.
- Fullscreen the target over the fallback screen when needed and restore its original fullscreen state afterward.

## Architecture

1. Attach to the existing Wayland, Hyprland, session D-Bus, and AT-SPI buses.
2. Reuse existing windows and application profiles.
3. Capture with `grim -T` and Hyprland's stable window ID.
4. Prefer AT-SPI controls for semantic interaction.
5. Use address-targeted shortcuts for discrete keyboard input.
6. Route native Wayland pointer actions directly to the selected surface.
7. Route XWayland pointer actions through XWayland's internal XTEST pointer and restore it afterward.
8. Verify operations with another exact capture.
9. Use a temporary headless output only for applications that reject the non-interfering routes.
10. On the fallback screen, fullscreen when needed and restore the original window, fullscreen, focus, workspace, and cursor state in cleanup.

## Boundary

This is not a second general-purpose Linux seat. Stock Hyprland still exposes one physical raw-pointer seat. Target-surface delivery covers ordinary app automation without using that seat; raw-input games, pointer-locked clients, remote desktops, and security surfaces may still require a focused fallback or an application-specific integration.
