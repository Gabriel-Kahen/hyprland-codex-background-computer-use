from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from same_session_computer_use.server import find_xwayland_display


def add_process(root: Path, pid: int, *, instance: str, wayland: str, display: str) -> None:
    process = root / str(pid)
    process.mkdir()
    (process / "comm").write_text("Xwayland\n")
    environment = {
        "HYPRLAND_INSTANCE_SIGNATURE": instance,
        "WAYLAND_DISPLAY": wayland,
        "DISPLAY": display,
    }
    (process / "environ").write_bytes(b"\0".join(f"{key}={value}".encode() for key, value in environment.items()))


class XWaylandDisplayTests(TestCase):
    def test_matches_the_selected_hyprland_instance(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            add_process(root, 101, instance="old", wayland="wayland-0", display=":0")
            add_process(root, 202, instance="current", wayland="wayland-1", display=":1")

            self.assertEqual(find_xwayland_display("current", "wayland-1", root), ":1")

    def test_ignores_unrelated_processes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            add_process(root, 101, instance="other", wayland="wayland-1", display=":9")
            unrelated = root / "202"
            unrelated.mkdir()
            (unrelated / "comm").write_text("python\n")
            (unrelated / "environ").write_bytes(b"DISPLAY=:2\0")

            self.assertIsNone(find_xwayland_display("current", "wayland-1", root))
