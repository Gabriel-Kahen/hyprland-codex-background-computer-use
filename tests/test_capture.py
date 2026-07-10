import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from same_session_computer_use import server


WINDOW = {"capture_id": "42", "address": "0x1", "size": [100, 100]}


class CaptureSaveTests(TestCase):
    def test_failed_capture_preserves_existing_destination(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "capture.png"
            destination.write_bytes(b"keep me")
            failed = subprocess.CompletedProcess([], 1, "", "grim failed")

            with patch.object(server, "resolve_window", return_value=WINDOW), patch.object(server, "run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "grim failed"):
                    server.capture_result({"window": "target", "save_path": str(destination)})

            self.assertEqual(destination.read_bytes(), b"keep me")

    def test_successful_capture_atomically_replaces_destination(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "capture.png"
            destination.write_bytes(b"old")

            def capture(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                Path(args[-1]).write_bytes(b"new image")
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(server, "resolve_window", return_value=WINDOW), patch.object(server, "run", side_effect=capture):
                result = server.capture_result({"window": "target", "save_path": str(destination)})

            self.assertEqual(destination.read_bytes(), b"new image")
            self.assertEqual(result["structuredContent"]["saved_to"], str(destination))
