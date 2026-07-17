# ============================================================
#  NibCast — guided VAD calibration tests
# ============================================================
#  Guards the /api/calibrate-vad/guided math: the computed threshold
#  must sit clearly above the ambient floor and clearly below typical
#  speech level, and hopeless inputs (silence, voice buried in noise)
#  must be rejected instead of producing a deaf or hair-trigger gate.
#
#  Run from project root:   python -m unittest tests.test_calibration
# ============================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from web_dashboard import _guided_threshold, _percentile


class GuidedThresholdTests(unittest.TestCase):

    def test_quiet_room_normal_voice(self):
        ambient = [0.005] * 30
        speech  = [0.02] * 10 + [0.17] * 40 + [0.19] * 10   # gaps + voice
        thr, extra = _guided_threshold(ambient, speech)
        self.assertIsNotNone(thr)
        self.assertGreater(thr, extra["ambient_floor"],
                           "threshold must sit above the ambient floor")
        self.assertLess(thr, extra["speech_level"],
                        "threshold must sit below typical speech level")

    def test_loud_room_high_gain_mic(self):
        # The setup from the field report: ambient ~0.10, voice ~0.17-0.18.
        ambient = [0.09, 0.10, 0.11] * 10
        speech  = [0.12] * 10 + [0.17] * 30 + [0.18] * 20
        thr, extra = _guided_threshold(ambient, speech)
        self.assertIsNotNone(thr, f"should calibrate, got error: {extra}")
        self.assertGreater(thr, 0.11, "must clear the loud ambient floor")
        # +0.0005 = the 3-decimal rounding grain the endpoint applies
        self.assertLessEqual(thr, extra["speech_level"] * 0.65 + 0.0005,
                             "wake phrase must still cross the gate comfortably")

    def test_threshold_clamped_to_engine_ceiling(self):
        ambient = [0.20] * 30
        speech  = [0.9] * 60
        thr, _ = _guided_threshold(ambient, speech)
        self.assertIsNotNone(thr)
        self.assertLessEqual(thr, config.WAKE_WORD_VAD_THRESHOLD_MAX)

    def test_rejects_silence_during_speech_phase(self):
        thr, err = _guided_threshold([0.004] * 30, [0.004] * 60)
        self.assertIsNone(thr)
        self.assertIn("No voice", err)

    def test_rejects_voice_buried_in_noise(self):
        thr, err = _guided_threshold([0.15] * 30, [0.16] * 60)
        self.assertIsNone(thr)
        self.assertIn("too close", err)

    def test_rejects_insufficient_samples(self):
        thr, err = _guided_threshold([0.01] * 2, [0.1] * 3)
        self.assertIsNone(thr)
        self.assertIn("Not enough", err)

    def test_ignores_garbage_samples(self):
        ambient = [0.005] * 30 + ["nan", None, -5, 7]
        speech  = [0.15] * 60 + [None, "x"]
        thr, extra = _guided_threshold(ambient, speech)
        self.assertIsNotNone(thr)

    def test_percentile_edges(self):
        self.assertEqual(_percentile([], 90), 0.0)
        self.assertEqual(_percentile([0.5], 90), 0.5)
        self.assertAlmostEqual(_percentile([0.1, 0.2, 0.3, 0.4], 0), 0.1)
        self.assertAlmostEqual(_percentile([0.1, 0.2, 0.3, 0.4], 100), 0.4)


if __name__ == "__main__":
    unittest.main()
