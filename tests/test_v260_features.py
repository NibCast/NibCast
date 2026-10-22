# ============================================================
#  NibCast — v2.6.0 feature tests
# ============================================================
#  • Hybrid race: cloud + local in parallel, first good answer
#    wins after a grace window; the better-scored text is kept
#  • Formatting by intent: spoken lists → numbered lists (also
#    without the LLM), Markdown tables only where they render
#  • Disfluency removal and grammar-aware scoring
#  • AI Prompt target (ChatGPT / Claude / Gemini …)
#
#  Run from project root:   python -m unittest tests.test_v260_features
# ============================================================

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import target_manager as tm
import text_processor as tp
from hybrid import race_two


class _Resp:
    def __init__(self, content):
        self.status_code = 200
        self.headers = {}
        self.text = content
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


class RaceTests(unittest.TestCase):

    def test_slow_engine_does_not_hold_up_result(self):
        t0 = time.time()
        a, b = race_two(lambda: "fast", lambda: (time.sleep(2.0), "slow")[1],
                        timeout=10, grace=0.3, usable=bool)
        self.assertEqual((a, b), ("fast", None))
        self.assertLess(time.time() - t0, 1.0)

    def test_both_fast_both_returned(self):
        a, b = race_two(lambda: "x", lambda: (time.sleep(0.1), "y")[1],
                        timeout=5, grace=1.0, usable=bool)
        self.assertEqual((a, b), ("x", "y"))

    def test_unusable_first_waits_for_other(self):
        a, b = race_two(lambda: "", lambda: (time.sleep(0.5), "late but good")[1],
                        timeout=5, grace=0.1, usable=bool)
        self.assertEqual(b, "late but good")

    def test_exception_counts_as_empty(self):
        def boom():
            raise RuntimeError("x")
        a, b = race_two(boom, lambda: "ok", timeout=2, grace=0.1, usable=bool)
        self.assertEqual((a, b), ("", "ok"))


class HybridCleanupTests(unittest.TestCase):

    def setUp(self):
        self._saved = {k: getattr(config, k) for k in
                       ("CLEAN_WITH_LLM", "LLM_BACKEND", "GROQ_API_KEY", "BRAIN_MODE", "LLM_BRAIN_SECONDARY",
                        "LLM_FALLBACK_BACKEND", "OLLAMA_BASE_URL", "OLLAMA_LLM_MODEL", "BRAIN_GRACE_SEC",
                        "WHISPER_PROMPT", "OFFLINE_MODE", "LLM_HYBRID")}
        config.CLEAN_WITH_LLM, config.LLM_BACKEND, config.GROQ_API_KEY = True, "groq", "k"
        config.BRAIN_MODE, config.LLM_BRAIN_SECONDARY, config.LLM_HYBRID = False, "ollama", True
        config.LLM_FALLBACK_BACKEND, config.WHISPER_PROMPT, config.OFFLINE_MODE = "", "", False
        config.OLLAMA_BASE_URL, config.OLLAMA_LLM_MODEL = "http://127.0.0.1:11434/v1", "qwen2.5:7b"
        config.BRAIN_GRACE_SEC = 0.3

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)

    def _post(self, cloud, local, local_delay=0.0):
        def post(url, **kw):
            if "11434" in url:
                time.sleep(local_delay)
                return _Resp(local)
            return _Resp(cloud)
        return post

    def test_better_local_result_wins(self):
        post = self._post(cloud="so um we we need the docs", local="We need the docs.")
        with mock.patch.object(tp.requests, "post", side_effect=post):
            p = tp.TextProcessor()
            out = p.clean("so um we we need the docs")
        self.assertEqual(out, "We need the docs.")
        self.assertEqual(p.last_status, "hybrid:ollama")

    def test_slow_local_falls_back_to_cloud_quickly(self):
        post = self._post(cloud="We need the docs.", local="We need the docs!", local_delay=2.0)
        t0 = time.time()
        with mock.patch.object(tp.requests, "post", side_effect=post):
            p = tp.TextProcessor()
            out = p.clean("we need the docs")
        self.assertEqual(out, "We need the docs.")
        self.assertEqual(p.last_status, "hybrid:groq")
        self.assertLess(time.time() - t0, 1.5)


class SpokenListTests(unittest.TestCase):
    B = staticmethod(tp.TextProcessor._basic_cleanup)

    def test_ordinals_become_numbered_list(self):
        self.assertEqual(
            self.B("um so for the release, first, update the docs, second, run the tests, and third, tag it"),
            "For the release:\n1. Update the docs.\n2. Run the tests.\n3. Tag it.")

    def test_ordinals_inside_prose_untouched(self):
        t = "The first quarter was good, the second was bad, and the third was great"
        self.assertEqual(self.B(t), t + ".")

    def test_number_one_two(self):
        self.assertEqual(self.B("okay uh number one buy milk number two call mom"),
                         "1. Buy milk.\n2. Call mom.")

    def test_ordinary_first_and_second_untouched(self):
        t = "at first I thought it was broken but wait a second it works"
        self.assertEqual(self.B(t), "At first I thought it was broken but wait a second it works.")

    def test_disfluencies_removed_but_words_kept(self):
        self.assertEqual(self.B("um, hmm. yes I agree. uh the plan works"), "Yes I agree. The plan works.")
        self.assertEqual(self.B("uh-huh that is 5 mm wide"), "Uh-huh that is 5 mm wide.")


class ScoringTests(unittest.TestCase):

    def test_table_is_not_penalised_as_padding(self):
        raw = "basic plan is ten dollars with one user pro is twenty dollars with five users"
        table = "| Plan | Price | Users |\n|---|---|---|\n| Basic | $10 | 1 |\n| Pro | $20 | 5 |"
        prose = "basic plan is ten dollars with one user pro is twenty dollars with five users"
        self.assertGreater(tp._score_cleanup(raw, table), tp._score_cleanup(raw, prose))

    def test_formatted_list_beats_prose_when_speaker_enumerated(self):
        raw = "first book flights second book hotel third rent a car"
        lst = "1. Book flights.\n2. Book hotel.\n3. Rent a car."
        prose = "First book flights, second book hotel, third rent a car."
        self.assertGreater(tp._score_cleanup(raw, lst), tp._score_cleanup(raw, prose))

    def test_leftover_stutter_and_lowercase_penalised(self):
        raw = "we need the the docs. then ship it"
        self.assertGreater(tp._score_cleanup(raw, "We need the docs. Then ship it."),
                           tp._score_cleanup(raw, "We need the the docs. then ship it."))


class PromptTests(unittest.TestCase):

    def test_flow_prompt_covers_requested_behaviour(self):
        p = tp._PROMPT_FLOW.lower()
        for phrase in ("kind of", "padding sentences", "grammatical", "numbered list",
                       "markdown table", "self-corrections"):
            self.assertIn(phrase, p)
        tp._PROMPT_FLOW.format(hint="")   # no stray braces


class TargetTests(unittest.TestCase):

    def _detect(self, title):
        with mock.patch.object(tm, "_active_window_name", return_value=title), \
             mock.patch.object(tm, "_manual_override", ""):
            return tm.detect_target()

    def test_ai_prompt_target(self):
        r = self._detect("chatgpt - google chrome")
        self.assertEqual(r["category"], "ai")
        self.assertIn("never answer it", r["llm_hint"])
        self.assertIn("Markdown is supported", r["llm_hint"])

    def test_markdown_only_where_it_renders(self):
        self.assertTrue(self._detect("project notes - notion")["markdown"])
        word = self._detect("report.docx - word")
        self.assertFalse(word["markdown"])
        self.assertIn("Plain text only", word["llm_hint"])

    def test_email_hint_structures_without_inventing(self):
        hint = self._detect("inbox - outlook")["llm_hint"]
        self.assertIn("own line", hint)
        self.assertIn("Never invent", hint)


class PrivacyGapTests(unittest.TestCase):

    def test_injector_log_line_redacted_and_scrubbed(self):
        import web_dashboard
        from logger import redact
        with mock.patch.object(config, "PRIVACY_MODE", True):
            self.assertNotIn("secret", redact("secret text"))
        self.assertNotIn("secret", web_dashboard._scrub_log_line("t [INFO] 📋 Injecting: 'secret text'\n"))


if __name__ == "__main__":
    unittest.main()
