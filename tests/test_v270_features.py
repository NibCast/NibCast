# ============================================================
#  NibCast — v2.7.0 feature tests
# ============================================================
#  • Local wake-word engine (keyword spotting) + its wiring into
#    the voice activator, and safe model-archive extraction
#  • Live text preview loop + caption trimming
#  • Pause/resume media around dictation
#  • Screen-context term extraction
#
#  The real keyword model is ~17 MB and not in the repo; the
#  end-to-end detection test runs only when NIBCAST_KWS_MODEL_DIR
#  points at an extracted model folder.
#
#  Run from project root:   python -m unittest tests.test_v270_features
# ============================================================

import io
import os
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
import wave
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import live_preview
import media_control
import screen_context
import wake_engine
from voice_activator import VoiceActivator


# ── Wake engine ───────────────────────────────────────────────

class SafeExtractTests(unittest.TestCase):

    def _tar(self, path, members):
        with tarfile.open(path, "w:bz2") as tf:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    def test_only_model_files_inside_dest(self):
        with tempfile.TemporaryDirectory() as d:
            arc = os.path.join(d, "m.tar.bz2")
            dest = os.path.join(d, "out")
            os.makedirs(dest)
            self._tar(arc, [
                (f"{wake_engine.MODEL_NAME}/tokens.txt", b"tok"),
                ("../evil.txt", b"x"),                                    # traversal
                (f"{wake_engine.MODEL_NAME}/../../tokens.txt", b"x"),     # traversal to a wanted name
                ("/abs/bpe.model", b"x"),                                 # absolute
                (f"{wake_engine.MODEL_NAME}/run.sh", b"x"),               # not a model file
            ])
            wake_engine._safe_extract(arc, dest)
            self.assertFalse(os.path.exists(os.path.join(d, "evil.txt")))
            self.assertFalse(os.path.exists(os.path.join(d, "tokens.txt")))
            got = sorted(os.listdir(os.path.join(dest, wake_engine.MODEL_NAME)))
            self.assertEqual(got, ["tokens.txt"])

    def test_phrase_cleaning(self):
        self.assertEqual(wake_engine._clean_phrase("Hey, Nova!! 2"), "HEY NOVA")
        self.assertEqual(wake_engine._clean_phrase("123"), "")

    def test_build_spotter_without_deps_returns_none(self):
        with mock.patch.object(config, "WAKE_WORD", "hey nova"), \
             mock.patch.object(wake_engine, "available", return_value=False):
            self.assertIsNone(wake_engine.build_spotter())


@unittest.skipUnless(os.environ.get("NIBCAST_KWS_MODEL_DIR"), "real KWS model not available")
class RealKeywordSpotterTests(unittest.TestCase):
    """End-to-end on the model's own sample: '…lamps would light up here…'."""

    def test_detects_phrase_in_real_speech(self):
        mdir = os.environ["NIBCAST_KWS_MODEL_DIR"]
        with mock.patch.object(wake_engine, "MODEL_DIR", mdir):
            k = wake_engine.KeywordSpotter(["light up", "hey nova"])
            wf = wave.open(os.path.join(mdir, "test_wavs", "0.wav"))
            a = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            hits = []
            for i in range(0, len(a), 1024):              # mic-sized blocks
                hits += k.accept(a[i:i + 1024], 16000)
        self.assertEqual(hits, ["light up"])


class _FakeRecorder:
    is_recording = False
    _actual_rate = 16000

    def set_monitor_hook(self, h): pass
    def open_persistent_stream(self): pass
    def close_persistent_stream(self): pass


class _FakeSpotter:
    """Reports the wake phrase when it sees a block of loud audio."""

    def __init__(self):
        self.resets = 0

    def accept(self, samples, rate):
        return ["hey nova"] if float(np.abs(samples).max()) > 0.5 else []

    def reset(self):
        self.resets += 1


class ActivatorKwsTests(unittest.TestCase):

    def setUp(self):
        self.starts, self.wakes = [], []
        self.woke = threading.Event()
        self.va = VoiceActivator(_FakeRecorder(), lambda: self.starts.append(1), lambda: None)
        self.va._running = True
        self.spot = _FakeSpotter()

        def on_wake(label):
            self.wakes.append(label)
            self.woke.set()
        self.va.attach_keyword_spotter(self.spot, on_wake)

    def tearDown(self):
        self.va._running = False
        self.va.attach_keyword_spotter(None, None)

    def _feed(self, level, n=10):
        for _ in range(n):
            self.va._on_energy(np.full((1024, 1), level, dtype=np.float32))

    def test_loud_speech_never_records_a_clip_in_sleep(self):
        self._feed(0.2, 40)                       # well above the VAD gate
        time.sleep(0.2)
        self.assertEqual(self.starts, [], "no Phase-1 clip recording with the local engine")

    def test_wake_phrase_calls_back(self):
        self._feed(0.9, 2)
        self.assertTrue(self.woke.wait(2.0))
        self.assertEqual(self.wakes, ["hey nova"])

    def test_ignored_in_command_mode_and_reset_on_sleep(self):
        self.va.set_mode("command")
        self._feed(0.0, 2)
        self.va.set_mode("sleep")
        self.assertGreaterEqual(self.spot.resets, 1)

    def test_detach_restores_clip_recording(self):
        self.va.attach_keyword_spotter(None, None)
        self.assertFalse(self.va.uses_keyword_spotter)


# ── Live preview ──────────────────────────────────────────────

class CaptionTests(unittest.TestCase):

    def test_short_text_unchanged(self):
        self.assertEqual(live_preview.caption_text("hello   world"), "hello world")

    def test_long_text_shows_tail_at_word_boundary(self):
        t = " ".join(f"word{i}" for i in range(100))
        c = live_preview.caption_text(t, max_chars=40)
        self.assertTrue(c.startswith("…word"))
        self.assertTrue(c.endswith("word99"))
        self.assertLessEqual(len(c), 41)


class _SnapRecorder:
    def __init__(self):
        self.calls = 0

    def snapshot_wav(self, last_sec=None):
        self.calls += 1
        return b"RIFF"


class LivePreviewLoopTests(unittest.TestCase):

    def test_updates_then_stops(self):
        texts = []
        rec = _SnapRecorder()
        with mock.patch.object(live_preview, "_engine", return_value=(lambda w: f"heard {rec.calls}", 0.02)), \
             mock.patch.object(live_preview, "MIN_AUDIO_SEC", 0.0):
            lp = live_preview.LivePreview(rec, texts.append)
            self.assertTrue(lp.start())
            time.sleep(0.25)
            lp.stop()
            time.sleep(0.1)
            n = len(texts)
            time.sleep(0.1)
        self.assertGreater(n, 1)
        self.assertEqual(len(texts), n, "no updates after stop()")

    def test_off_means_no_thread(self):
        with mock.patch.object(config, "LIVE_PREVIEW", "off"):
            self.assertFalse(live_preview.LivePreview(_SnapRecorder(), print).start())

    def test_auto_without_local_whisper_does_nothing(self):
        import local_asr
        with mock.patch.object(config, "LIVE_PREVIEW", "auto"), \
             mock.patch.object(local_asr, "is_loaded", return_value=False):
            self.assertEqual(live_preview._engine(), (None, 0))


# ── Media pause ───────────────────────────────────────────────

class _FakeMedia:
    def __init__(self):
        self.playing = {"Spotify": True, "Chrome": False}
        self.resumed = []

    async def pause_playing(self):
        ids = [k for k, v in self.playing.items() if v]
        for k in ids:
            self.playing[k] = False
        return ids

    async def resume(self, ids):
        self.resumed += ids
        return len(ids)


class MediaPauserTests(unittest.TestCase):

    def _wait(self, cond):
        for _ in range(100):
            if cond():
                return
            time.sleep(0.01)

    def test_resumes_only_what_it_paused(self):
        fake = _FakeMedia()
        p = media_control.MediaPauser(backend=fake)
        with mock.patch.object(config, "PAUSE_MEDIA_WHILE_DICTATING", True):
            p.pause()
            p.resume()
            self._wait(lambda: fake.resumed)
        self.assertEqual(fake.resumed, ["Spotify"])

    def test_disabled_does_nothing(self):
        fake = _FakeMedia()
        p = media_control.MediaPauser(backend=fake)
        with mock.patch.object(config, "PAUSE_MEDIA_WHILE_DICTATING", False):
            p.pause()
            p.resume()
            time.sleep(0.1)
        self.assertTrue(fake.playing["Spotify"])
        self.assertEqual(fake.resumed, [])

    def test_unavailable_off_windows(self):
        if sys.platform != "win32":
            self.assertFalse(media_control.available())


# ── Screen context ────────────────────────────────────────────

class ScreenContextTests(unittest.TestCase):

    TEXT = ("Re: Kubernetes migration plan - Priyanka Sharma - Outlook\n"
            "Hi team, the NibCast rollout on AWS EKS is after the GPT4o eval. "
            "@devops-oncall please review. File Edit View Help Today Monday The plan is ready.")

    def test_extracts_names_terms_handles(self):
        terms = screen_context.extract_terms(self.TEXT)
        for t in ("Kubernetes", "Priyanka", "Sharma", "NibCast", "AWS", "EKS", "GPT4o", "@devops-oncall"):
            self.assertIn(t, terms)
        for ui in ("File", "Edit", "View", "Help", "Today", "Monday", "The"):
            self.assertNotIn(ui, terms)

    def test_limit_and_frequency_order(self):
        terms = screen_context.extract_terms("Zed Alpha Alpha Alpha Beta Beta", limit=2)
        self.assertEqual(terms, ["Alpha", "Beta"])

    def test_hint_forbids_inserting(self):
        self.assertIn("never insert", screen_context.prompt_hint(["Priyanka"]))
        self.assertEqual(screen_context.prompt_hint([]), "")

    def test_capture_runs_in_background(self):
        with mock.patch.object(screen_context, "capture", return_value=["Priyanka"]):
            c = screen_context.Capture()
            self.assertEqual(c.terms(timeout=1.0), ["Priyanka"])


if __name__ == "__main__":
    unittest.main()
