# ============================================================
#  NibCast — wake-gate (sleep-mode VAD) regression tests
# ============================================================
#  Guards against the v2.4.0 bug where the engine silently clamped
#  WAKE_WORD_VAD_THRESHOLD to 0.08 while loud-room ambient sits at
#  0.10–0.17 RMS — so raising the threshold did nothing and the app
#  recorded background audio continuously.
#
#  Run from project root:   python -m unittest tests.test_vad_gate
# ============================================================

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _DummyRecorder:
    is_recording = False

    def set_monitor_hook(self, cb): pass
    def open_persistent_stream(self): pass
    def close_persistent_stream(self): pass


def _block(rms):
    # Constant-amplitude block → RMS equals the amplitude exactly.
    return np.full((1024, 1), rms, dtype=np.float32)


class _GateHarness:
    """Feeds synthetic RMS blocks into VoiceActivator._on_energy and reports
    whether a recording fired."""

    def __init__(self, cfg_thr, seed_floor=None):
        import config
        from voice_activator import VoiceActivator
        self._config = config
        self.started = []
        self.va = VoiceActivator(_DummyRecorder(),
                                 lambda: self.started.append(1), lambda: None)
        self.va._running = True
        self.va._mode = "sleep"
        if seed_floor is not None:
            self.va._noise_floor = seed_floor
        config.WAKE_WORD_VAD_THRESHOLD = cfg_thr

    def feed(self, rms, blocks=40, dt=0.005):
        self.started.clear()
        self.va._is_recording = False
        for _ in range(blocks):
            self.va._on_energy(_block(rms))
            time.sleep(dt)
        return bool(self.started)


class TestWakeGate(unittest.TestCase):
    """Behavioral contract of the sleep-mode wake gate."""

    def setUp(self):
        import config
        self._saved = {k: getattr(config, k) for k in
                       ("WAKE_WORD_VAD_THRESHOLD", "WAKE_WORD_TRIGGER_SEC")}
        # Short trigger window so each scenario needs only ~0.2 s of audio.
        config.WAKE_WORD_TRIGGER_SEC = 0.05

    def tearDown(self):
        import config
        for k, v in self._saved.items():
            setattr(config, k, v)

    def test_configured_threshold_above_008_is_honored(self):
        # The v2.4.0 bug: gate clamped to 0.08 → 0.12 ambient always fired.
        h = _GateHarness(cfg_thr=0.15)
        self.assertFalse(h.feed(0.12), "ambient below configured threshold must not trigger")
        self.assertTrue(h.feed(0.25), "voice above configured threshold must trigger")

    def test_noise_floor_raises_gate_in_loud_room(self):
        # Default low threshold, but measured ambient floor 0.10 → gate floats
        # to 0.15 and steady 0.12 background no longer triggers.
        h = _GateHarness(cfg_thr=0.03, seed_floor=0.10)
        self.assertFalse(h.feed(0.12), "steady ambient must be absorbed by the adaptive gate")
        self.assertTrue(h.feed(0.28), "voice well above ambient must still trigger")

    def test_ceiling_guards_corrupt_config(self):
        # A corrupt/oversized value is clamped to WAKE_WORD_VAD_THRESHOLD_MAX so
        # the wake word stays physically possible to say.
        import config
        h = _GateHarness(cfg_thr=0.9)
        self.assertTrue(h.feed(config.WAKE_WORD_VAD_THRESHOLD_MAX + 0.05),
                        "voice above the ceiling must trigger even with a corrupt config")

    def test_quiet_mic_setup_unchanged(self):
        # Regression: defaults on a quiet mic (room tone 0.005, voice 0.06).
        h = _GateHarness(cfg_thr=0.03, seed_floor=0.005)
        self.assertFalse(h.feed(0.005), "room tone must not trigger")
        self.assertTrue(h.feed(0.06), "a quiet voice must still cross the default gate")


class TestUiMatchesEngine(unittest.TestCase):
    """The dashboard must never offer values the engine silently clamps away —
    that mismatch is exactly how the v2.4.0 bug shipped."""

    def _slider(self, element_id):
        html = open(os.path.join(_PROJECT_ROOT, "templates", "dashboard.html"),
                    encoding="utf-8").read()
        m = re.search(r'<input[^>]*min="([\d.]+)"[^>]*max="([\d.]+)"[^>]*id="%s"'
                      % element_id, html)
        self.assertIsNotNone(m, f"slider #{element_id} not found in dashboard.html")
        return float(m.group(1)), float(m.group(2))

    def test_threshold_slider_max_matches_engine_ceiling(self):
        import config
        _, slider_max = self._slider("cfgVadThreshold")
        self.assertEqual(slider_max, config.WAKE_WORD_VAD_THRESHOLD_MAX,
                         "dashboard threshold slider max must equal "
                         "config.WAKE_WORD_VAD_THRESHOLD_MAX")

    def test_wake_silence_slider_min_matches_engine_floor(self):
        # voice_activator.py sleep branch: silence_sec = max(0.3, …)
        slider_min, _ = self._slider("cfgWakeSilenceSec")
        self.assertGreaterEqual(slider_min, 0.3,
                                "slider must not offer silence values below the "
                                "engine floor (0.3 s) — they would be dead zone")


if __name__ == "__main__":
    unittest.main()
