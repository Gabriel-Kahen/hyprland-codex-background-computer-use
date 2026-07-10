from unittest import TestCase

from same_session_computer_use.server import png_pixel_size, window_coordinate_space


def png_header(width: int, height: int) -> bytes:
    raw = bytearray(24)
    raw[:8] = b"\x89PNG\r\n\x1a\n"
    raw[16:20] = width.to_bytes(4, "big")
    raw[20:24] = height.to_bytes(4, "big")
    return bytes(raw)


class CoordinateSpaceTests(TestCase):
    def test_reads_png_dimensions(self) -> None:
        self.assertEqual(png_pixel_size(png_header(712, 640)), {"width": 712, "height": 640})

    def test_reports_pixel_to_window_transform_for_scaled_capture(self) -> None:
        space = window_coordinate_space({"size": [356, 320]}, png_header(712, 640))

        self.assertIsNotNone(space)
        self.assertEqual(space["pixel_to_window_scale"], {"x": 0.5, "y": 0.5})

    def test_rejects_non_png_data(self) -> None:
        self.assertIsNone(window_coordinate_space({"size": [356, 320]}, b"not a png"))
