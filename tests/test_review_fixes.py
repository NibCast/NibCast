# ============================================================
#  NibCast — regression tests for the v2.7.0 code/security review
# ============================================================
#  One test (or more) per verified review finding, so each fix
#  stays fixed.
#
#  Run from project root:   python -m unittest tests.test_review_fixes
# ============================================================

import os
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import media_control
import target_manager as tm
import text_processor as tp
from voice_activator import VoiceActivator


class _Resp:
    def __init__(self, status, content=""):
        self.status_code = status
        self.headers = {}
        self.text = content
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}],
                "content": [{"text": self._c}]}


def _cfg(**kw):
    return mock.patch.multiple(config, **kw)


class OfflineAnthropicTests(unittest.TestCase):
    """Security review: Offline Mode was bypassed for the Anthropic backend."""

    def test_cleanup_never_calls_anthropic_offline(self):
        with _cfg(OFFLINE_MODE=True, LLM_BACKEND="anthropic", ANTHROPIC_API_KEY="k",
                  LLM_BRAIN_SECONDARY="", LLM_FALLBACK_BACKEND="", CLEAN_WITH_LLM=True,
                  WHISPER_PROMPT=""), \
             mock.patch.object(tp.requests, "post") as post:
            p = tp.TextProcessor()
            out = p.clean("hello there")
        post.assert_not_called()
        self.assertEqual(out, "Hello there.")
        self.assertEqual(p.last_status, "basic:offline")

    def test_hybrid_secondary_anthropic_blocked_offline(self):
        with _cfg(OFFLINE_MODE=True, LLM_BACKEND="ollama", OLLAMA_BASE_URL="http://127.0.0.1:11434/v1",
                  LLM_BRAIN_SECONDARY="anthropic", LLM_HYBRID=True, ANTHROPIC_API_KEY="k", CLEAN_WITH_LLM=True,
                  WHISPER_PROMPT="", BRAIN_GRACE_SEC=0.2), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Hello there.")) as post:
            tp.TextProcessor().clean("hello there")
        for call in post.call_args_list:
            self.assertIn("127.0.0.1", call.args[0])

    def test_command_mode_blocked_offline(self):
        with _cfg(OFFLINE_MODE=True, LLM_BACKEND="anthropic", ANTHROPIC_API_KEY="k"), \
             mock.patch.object(tp.requests, "post") as post:
            p = tp.TextProcessor()
            self.assertEqual(p.command("selected", "make it formal"), "selected")
        post.assert_not_called()
        self.assertEqual(p.last_status, "basic:offline")


class TextRuleTests(unittest.TestCase):

    def test_repeated_place_names_survive(self):
        f = tp._fix_duplicates
        for t in ("We went to Bora Bora", "Walla Walla Washington", "New York, New York"):
            self.assertEqual(f(t), t)
        self.assertEqual(f("I I want"), "I want")

    def test_rewake_check_ignores_case_rule(self):
        self.assertEqual(tp._fix_duplicates("Hey Nova Hey Nova", keep_names=False), "Hey Nova")

    def test_prose_with_ordinals_not_listed(self):
        t = "The first quarter was good, the second was bad, and the third was great"
        self.assertEqual(tp._format_spoken_list(t), t)

    def test_tokens_that_look_like_disfluencies_kept(self):
        self.assertEqual(tp._strip_disfluencies(".NET is great"), ".NET is great")
        self.assertEqual(tp._strip_disfluencies("I said UM is a school"), "I said UM is a school")
        self.assertEqual(tp._strip_disfluencies("... and then it stopped"), "... and then it stopped")


class HybridTests(unittest.TestCase):

    def test_hybrid_failure_reason_is_kept(self):
        with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_BRAIN_SECONDARY="cerebras", LLM_HYBRID=True,
                  CEREBRAS_API_KEY="k", CLEAN_WITH_LLM=True, WHISPER_PROMPT="", HTTP_RETRIES=1,
                  BRAIN_GRACE_SEC=0.2), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(429)):
            p = tp.TextProcessor()
            p.clean("hello there")
        self.assertEqual(p.last_status, "basic:rate_limited")

    def test_hybrid_engines_do_not_stream(self):
        q = tp.subscribe_stream()
        try:
            with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_BRAIN_SECONDARY="cerebras", LLM_HYBRID=True,
                      CEREBRAS_API_KEY="k", CLEAN_WITH_LLM=True, WHISPER_PROMPT="", BRAIN_GRACE_SEC=0.2), \
                 mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Hello there.")) as post:
                tp.TextProcessor().clean("hello there")
            for call in post.call_args_list:
                self.assertFalse(call.kwargs["json"]["stream"])
        finally:
            tp.unsubscribe_stream(q)

    def test_hybrid_cleanup_does_not_need_brain_mode(self):
        with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_BRAIN_SECONDARY="cerebras", LLM_HYBRID=True,
                  CEREBRAS_API_KEY="k", BRAIN_MODE=False, CLEAN_WITH_LLM=True, WHISPER_PROMPT="",
                  BRAIN_GRACE_SEC=0.2), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Hello there.")) as post:
            p = tp.TextProcessor()
            p.clean("hello there")
        self.assertEqual(post.call_count, 2)
        self.assertTrue(p.last_status.startswith("hybrid:"))

    def test_command_mode_sets_its_own_status(self):
        with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k"), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Selected, formally.")):
            p = tp.TextProcessor()
            p.last_status = "basic:rate_limited"      # left over from a previous dictation
            p.command("selected", "make it formal")
        self.assertEqual(p.last_status, "llm")


class _Rec:
    is_recording = False
    _actual_rate = 16000
    def set_monitor_hook(self, h): pass
    def open_persistent_stream(self): pass
    def close_persistent_stream(self): pass


class _Spot:
    def accept(self, samples, rate):
        return ["hey nova"] if float(np.abs(samples).max()) > 0.5 else []

    def reset(self):
        pass


class KeywordSpotterWiringTests(unittest.TestCase):

    def _va(self):
        va = VoiceActivator(_Rec(), lambda: None, lambda: None)
        va._running = True
        return va

    def test_wake_does_not_block_command_recording(self):
        va = self._va()
        woke = threading.Event()
        va.attach_keyword_spotter(_Spot(), lambda label: woke.set())
        va._on_energy(np.full((1024, 1), 0.9, dtype=np.float32))
        self.assertTrue(woke.wait(2.0))
        self.assertLess(va._cooldown_until, time.time() + 0.01,
                        "a local wake must not arm the VAD cooldown")
        va.stop()

    def test_worker_survives_detach_reattach(self):
        va = self._va()
        va.attach_keyword_spotter(_Spot(), lambda l: None)
        t1 = va._kws_thread
        va.attach_keyword_spotter(None, None)
        time.sleep(0.7)                       # longer than the worker's idle timeout
        woke = threading.Event()
        va.attach_keyword_spotter(_Spot(), lambda l: woke.set())
        self.assertTrue(va._kws_thread.is_alive())
        self.assertIs(va._kws_thread, t1)
        va._on_energy(np.full((1024, 1), 0.9, dtype=np.float32))
        self.assertTrue(woke.wait(2.0))
        va.stop()


class DoubleTapAltGrTests(unittest.TestCase):

    def test_injected_left_ctrl_does_not_cancel_altgr_double_tap(self):
        try:
            import hotkey_listener as hk
        except Exception:                 # pynput needs a display on Linux
            self.skipTest("pynput unavailable")
        d = hk.DoubleTap()
        for t in (0.0, 0.25):
            d.press("ctrl_l", "alt_r", t)        # Windows' fake Ctrl before AltGr
            d.press("alt_r", "alt_r", t + 0.001)
            fired = d.release("alt_r", "alt_r", t + 0.05)
        self.assertTrue(fired)


class TargetOrderTests(unittest.TestCase):

    def _cat(self, title):
        with mock.patch.object(tm, "_active_window_name", return_value=title), \
             mock.patch.object(tm, "_manual_override", ""):
            return tm.detect_target()["category"]

    def test_email_with_ai_word_stays_email(self):
        self.assertEqual(self._cat("gmail - claude.smith@acme.com - google chrome"), "email")
        self.assertEqual(self._cat("re: gemini launch - outlook"), "email")

    def test_ai_tab_still_ai(self):
        self.assertEqual(self._cat("chatgpt - google chrome"), "ai")


class MediaOrderTests(unittest.TestCase):

    def test_slow_pause_still_resumed(self):
        class Slow:
            def __init__(self):
                self.resumed = []

            async def pause_playing(self):
                time.sleep(0.3)                 # slower than the dictation
                return ["Spotify"]

            async def resume(self, ids):
                self.resumed += ids
                return len(ids)
        b = Slow()
        p = media_control.MediaPauser(backend=b)
        with _cfg(PAUSE_MEDIA_WHILE_DICTATING=True):
            p.pause()
            p.resume()                          # called before the pause finished
            self.assertTrue(p.flush())
        self.assertEqual(b.resumed, ["Spotify"])


class DashboardReviewTests(unittest.TestCase):

    def setUp(self):
        import web_dashboard
        import local_models
        self.wd = web_dashboard
        self.client = web_dashboard.app.test_client()
        self._p = [mock.patch.object(web_dashboard.auth, "is_configured", return_value=True),
                   mock.patch.object(config, "save"),
                   mock.patch.object(local_models, "warm_up_async")]
        for p in self._p:
            p.start()
        with self.client.session_transaction() as s:
            s["vf_auth"] = True

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_as_hybrid_does_not_enable_dual_asr(self):
        with _cfg(BRAIN_MODE=False, LLM_BRAIN_SECONDARY="", LLM_HYBRID=False, OLLAMA_BASE_URL="", OLLAMA_LLM_MODEL=""):
            r = self.client.post("/api/local-models/use", json={
                "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b", "role": "hybrid"})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(config.BRAIN_MODE)
            self.assertEqual(config.LLM_BRAIN_SECONDARY, "ollama")
            self.assertTrue(config.LLM_HYBRID)

    def test_changing_whisper_model_reloads_it(self):
        import local_asr
        with _cfg(LOCAL_WHISPER_MODEL="small.en", LOCAL_WHISPER_DEVICE="auto"), \
             mock.patch.object(local_asr, "is_loaded", return_value=True), \
             mock.patch.object(local_asr, "load_async") as load:
            self.client.post("/api/config", json={"LOCAL_WHISPER_MODEL": "base.en"})
        load.assert_called_once()

    def test_multiline_old_log_entries_scrubbed(self):
        log = ["2026-10-01 10:00:00,1 [INFO] 📝 Pasting: '1. Book flights\n",
               "2. Call Dr. Smith about results' → Chrome\n",
               "2026-10-01 10:00:01,1 [ERROR] boom\n",
               "Traceback (most recent call last):\n"]
        out = self.wd._scrub_log(log)
        self.assertNotIn("Smith", out)
        self.assertNotIn("flights", out)
        self.assertIn("Traceback", out)

    def test_wake_3b_and_snippet_lines_scrubbed(self):
        s = self.wd._scrub_log_line
        self.assertNotIn("noah", s("2026-10-01 10:00:00,1 [INFO] 🔑 Wake fuzzy 3b: first exact + "
                                   "last Jaro=0.82 ('noah'≈'nova')\n"))
        self.assertNotIn("Elm", s("2026-10-01 10:00:00,1 [INFO] 📎 Snippet: 'my address' → '12 Elm St'\n"))


class RoundTwoTests(unittest.TestCase):
    """Second review pass (on the fixes themselves)."""

    def test_secondary_alone_does_not_turn_on_hybrid(self):
        with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_BRAIN_SECONDARY="openai",
                  OPENAI_API_KEY="k", LLM_HYBRID=False, CLEAN_WITH_LLM=True, WHISPER_PROMPT=""), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Hello there.")) as post:
            tp.TextProcessor().clean("hello there")
        self.assertEqual(post.call_count, 1)
        self.assertIn("groq", post.call_args.args[0])

    def test_old_config_migrates_hybrid_flag(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            for brain, sec, want in ((True, "ollama", True), (False, "openai", False)):
                with open(path, "w") as f:
                    json.dump({"BRAIN_MODE": brain, "LLM_BRAIN_SECONDARY": sec}, f)
                with mock.patch.object(config, "CONFIG_FILE", path), \
                     mock.patch.multiple(config, BRAIN_MODE=False, LLM_BRAIN_SECONDARY="", LLM_HYBRID=False):
                    config.load()
                    self.assertEqual(config.LLM_HYBRID, want)

    def test_unpunctuated_list_still_formatted(self):
        self.assertEqual(
            tp.TextProcessor._basic_cleanup("for the release first update the docs second run the tests and third tag it"),
            "For the release:\n1. Update the docs.\n2. Run the tests.\n3. Tag it.")

    def test_items_ending_in_that_or_this_ok(self):
        self.assertEqual(tp._format_spoken_list("First, check the logs. Second, fix that. Third, deploy this"),
                         "1. Check the logs.\n2. Fix that.\n3. Deploy this.")

    def test_ranking_prose_untouched(self):
        t = "I came first, he came second, and she came third"
        self.assertEqual(tp._format_spoken_list(t), t)

    def test_stretched_fillers_removed(self):
        self.assertEqual(tp._strip_disfluencies("uuh I think, mmhm, errm yes"), "I think, yes")

    def test_two_item_explicit_list_counts_for_scoring(self):
        self.assertTrue(tp._enumerates("number one eggs number two milk"))

    def test_unchanged_command_result_is_success(self):
        with _cfg(OFFLINE_MODE=False, LLM_BACKEND="groq", GROQ_API_KEY="k"), \
             mock.patch.object(tp.requests, "post", return_value=_Resp(200, "Already correct.")):
            p = tp.TextProcessor()
            self.assertEqual(p.command("Already correct.", "fix the grammar"), "Already correct.")
        self.assertEqual(p.last_status, "llm")

    def test_scrub_drops_leading_continuation_and_empty_marker_lines(self):
        import web_dashboard
        out = web_dashboard._scrub_log([
            "the rest of a dictation about Dr. Smith\n",                  # tail starts mid-entry
            "2026-10-01 10:00:00,1 [INFO] 📝 Pasting:\n",                  # text on the next lines
            "my bank PIN is in the drawer\n",
            "2026-10-01 10:00:01,1 [INFO] ✅ Database ready\n",
        ])
        self.assertNotIn("Smith", out)
        self.assertNotIn("drawer", out)
        self.assertIn("Database ready", out)

    def test_hung_media_call_does_not_block_queue(self):
        class Hung:
            def __init__(self):
                self.resumed = []

            async def pause_playing(self):
                import asyncio
                await asyncio.sleep(60)            # never returns in time

            async def resume(self, ids):
                self.resumed += ids
                return len(ids)
        with mock.patch.object(media_control, "_OP_TIMEOUT", 0.2), _cfg(PAUSE_MEDIA_WHILE_DICTATING=True):
            p = media_control.MediaPauser(backend=Hung())
            p.pause()
            p.resume()
            self.assertTrue(p.flush(timeout=3.0), "queue drained despite a hung pause")


if __name__ == "__main__":
    unittest.main()
