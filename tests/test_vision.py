import unittest

from autoclip.models import ManualCropOverride
from autoclip.vision import CoordinateMapper, Detection, build_reframe_track, crop_geometry
from tests.helpers import mt


class VisionTests(unittest.TestCase):
    def test_proxy_coordinate_mapping_is_deterministic(self) -> None:
        mapper = CoordinateMapper(1920, 1080, 640, 360)
        self.assertEqual(mapper.proxy_to_source(320, 90), (960.0, 270.0))
        self.assertEqual(mapper.normalized_proxy_to_source_normalized(.5, .25), (.5, .25))

    def test_smoothing_and_lost_subject_hold(self) -> None:
        timestamps = [mt(i) for i in range(5)]
        detections = [Detection(timestamps[0], .8, .5, .9), None, None, None, None]
        track = build_reframe_track("r1", timestamps, detections, smoothing_alpha=.5, max_lost_frames=2)
        self.assertLess(track.points[0].crop_x, .8)
        self.assertEqual(track.points[1].subject_x, track.points[0].crop_x)
        self.assertEqual(track.smoothing["lost_behavior"], "hold_then_ease_to_center")

    def test_manual_fixed_crop_override(self) -> None:
        override = ManualCropOverride(True, .2, .4, 1.2)
        track = build_reframe_track("r1", [mt(0)], [Detection(mt(0), .9, .9, 1)], manual_override=override, smoothing_alpha=1)
        self.assertEqual((track.points[0].crop_x, track.points[0].crop_y, track.points[0].scale), (.2, .4, 1.2))

    def test_vertical_crop_is_bounded(self) -> None:
        x, y, width, height = crop_geometry(1920, 1080, .95, .5)
        self.assertEqual((width, height), (608, 1080))
        self.assertLessEqual(x + width, 1920)
        self.assertLessEqual(y + height, 1080)


if __name__ == "__main__":
    unittest.main()

