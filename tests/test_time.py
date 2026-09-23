import unittest
from fractions import Fraction

from autoclip.time import COMMON_RATES, MediaTime, Rational, assess_frame_rate


class MediaTimeTests(unittest.TestCase):
    def test_all_required_rates_round_trip_without_drift(self) -> None:
        for label, fps in COMMON_RATES.items():
            with self.subTest(rate=label):
                rate = Rational.from_fraction(fps)
                original = 100_000
                timestamp = MediaTime.from_frames(original, rate)
                self.assertEqual(timestamp.to_frames(rate), original)
                self.assertEqual(timestamp.seconds, Fraction(original, 1) / fps)

    def test_rescale_exact(self) -> None:
        value = MediaTime(30000, Rational(1, 30000))
        self.assertEqual(value.rescale(Rational(1, 1000)).value, 1000)

    def test_external_seconds_can_be_quantized_once_to_source_ticks(self) -> None:
        value = MediaTime.from_seconds("1.23456", Rational(1, 30000), exact=False)
        self.assertEqual(value.value, 37037)

    def test_vfr_detection_documents_fallback(self) -> None:
        assessment = assess_frame_rate("30000/1001", "60/1")
        self.assertTrue(assessment.variable_frame_rate)
        self.assertIn("timestamp-aware", assessment.fallback or "")

    def test_cfr_not_mislabeled(self) -> None:
        self.assertFalse(assess_frame_rate("60000/1001", "60000/1001").variable_frame_rate)


if __name__ == "__main__":
    unittest.main()
