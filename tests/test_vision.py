import unittest
from pathlib import Path
from unittest.mock import patch

from autoclip.models import ManualCropOverride
from autoclip.vision import CoordinateMapper, Detection, analyze_video_clip, build_reframe_track, crop_geometry
from tests.helpers import mt


class VisionTests(unittest.TestCase):
    def test_real_media_sampling_uses_media_time_base(self) -> None:
        class Capture:
            def isOpened(self): return True
            def set(self, *_args): return True
            def read(self): return True, object()
            def release(self): pass

        class CV2:
            CAP_PROP_POS_MSEC = 0
            def VideoCapture(self, _path): return Capture()

        class Detector:
            _cv2 = CV2()
            def detect(self, frames): return [None for _ in frames]

        with patch("autoclip.vision.OpenCVFaceDetector", return_value=Detector()):
            track = analyze_video_clip(Path("fixture.mp4"), mt(0), mt(1), "track", sample_interval=.5)
        self.assertEqual(len(track.points), 3)
        self.assertEqual(track.points[0].at.time_base, mt(0).time_base)

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
        self.assertEqual(track.smoothing["lost_behavior"], "hold_fixed")
        self.assertEqual({point.crop_x for point in track.points[1:]}, {track.points[0].crop_x})

    def test_weak_or_small_detection_changes_hold_stable_crop(self) -> None:
        timestamps = [mt(0), mt(1), mt(2)]
        track = build_reframe_track("r1", timestamps, [None, Detection(timestamps[1], .53, .5, .95), Detection(timestamps[2], .9, .5, .2)])
        self.assertEqual([point.crop_x for point in track.points], [.5, .5, .5])

    def test_crop_motion_is_velocity_limited(self) -> None:
        timestamps = [mt(0), mt(1)]
        track = build_reframe_track("r1", timestamps, [Detection(timestamps[0], .95, .5, 1), Detection(timestamps[1], .05, .5, 1)], smoothing_alpha=1, max_velocity_per_second=.1)
        self.assertLessEqual(abs(track.points[1].crop_x - track.points[0].crop_x), .100001)

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
