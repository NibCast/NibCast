# ============================================================
#  NibCast — v2.8.0 output-quality tests
# ============================================================
#  • Fidelity metric + scorer prefer faithful edits over rewrites
#  • Fidelity guard re-runs a drifting cleanup in verbatim style
#  • Reasoning models get low effort / bigger budget; empty
#    content is retried once instead of silently pasted raw
#  • LLM_AUTO_SELECT: discovery, ranking, racing, stats
#  • Voice-mode "ignored" clips are surfaced, not silent
#
#  Run from project root:   python -m unittest tests.test_v280_quality
# ============================================================

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import hybrid
import text_processor as tp

RAW = ("so um after the after the recent update I don't know what happened to the "
       "app but the output quality has been quite degraded maybe once you can check "
       "the logs of the last one month and most of them has like low score")
FAITHFUL = ("After the recent update, I don't know what happened to the app, but the "
            "output quality has been quite degraded. Maybe once you can check the logs "
            "of the last month. Most of them have a low score.")
REWRITE = ("The app's quality dropped after the update. Please review last month's "
           "logs, which show low scores.")


class _Resp:
    def __init__(self, code=200, content="", body=None, headers=None):
        self.status_code = code
        self._body = body if body is not None else {"choices": [{"message": {"content": content}}]}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FidelityTests(unittest.TestCase):
    def test_faithful_edit_scores_high_rewrite_low(self):
        self.assertGreater(tp._fidelity(RAW, FAITHFUL), 0.8)
        self.assertLess(tp._fidelity(RAW, REWRITE), 0.5)

    def test_fillers_and_stutters_do_not_count_as_loss(self):
        self.assertGreater(tp._fidelity("um I I want the the blue one you know", "I want the blue one."), 0.95)

    def test_scorer_prefers_faithful_over_polished_rewrite(self):
        self.assertGreater(tp._score_cleanup(RAW, FAITHFUL), tp._score_cleanup(RAW, REWRITE))

    def test_new_flow_prompt_formats(self):
        p = tp._PROMPT_FLOW.format(hint="")
        self.assertIn("EDIT, DON'T REWRITE", p)
        self.assertNotIn("{hint}", p)


class ReasoningModelTests(unittest.TestCase):
    def test_params(self):
        self.assertEqual(tp._reasoning_params("openai/gpt-oss-120b", "https://api.groq.com/openai/v1/chat/completions"),
                         {"reasoning_effort": "low", "include_reasoning": False})
        self.assertEqual(tp._reasoning_params("llama-3.3-70b-versatile", "https://api.groq.com/x"), {})
        self.assertGreater(tp._max_tokens_for("word " * 300, "openai/gpt-oss-120b"),
                           tp._max_tokens_for("word " * 300, "llama-3.3-70b"))

    def test_empty_content_is_retried_with_bigger_budget(self):
        calls = []
        def post(url, headers=None, json=None, timeout=None):
            calls.append(dict(json))
            return _Resp(content="" if len(calls) == 1 else "Cleaned text here.")
        p = tp.TextProcessor()
        with mock.patch.object(tp.requests, "post", side_effect=post), \
             mock.patch.object(config, "HTTP_RETRIES", 3):
            out = p._clean_openai_compat("sys", "cleaned text here", url="https://api.groq.com/openai/v1/chat/completions",
                                         key="k", model="openai/gpt-oss-120b", backend_name="Groq", allow_stream=False)
        self.assertEqual(out, "Cleaned text here.")
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1]["max_tokens"], calls[0]["max_tokens"])

    def test_rejected_reasoning_options_are_dropped(self):
        calls = []
        def post(url, headers=None, json=None, timeout=None):
            calls.append(dict(json))
            if "reasoning_effort" in json:
                return _Resp(400, body={"error": {"message": "unknown parameter"}})
            return _Resp(content="Hello there, friend.")
        with mock.patch.object(tp.requests, "post", side_effect=post):
            out = tp.TextProcessor()._clean_openai_compat(
                "sys", "hello there friend", url="https://api.cerebras.ai/v1/chat/completions",
                key="k", model="gpt-oss-120b", backend_name="Cerebras", allow_stream=False)
        self.assertEqual(out, "Hello there, friend.")
        self.assertNotIn("reasoning_effort", calls[-1])


class FidelityGuardTests(unittest.TestCase):
    def test_drifting_cleanup_rerun_verbatim(self):
        prompts = []
        def run(self_, system_prompt, raw_text, backend_name, allow_stream=True, model=None):
            prompts.append(system_prompt)
            return REWRITE if len(prompts) == 1 else FAITHFUL
        with mock.patch.object(tp.TextProcessor, "_run_backend", run), \
             mock.patch.multiple(config, CLEAN_WITH_LLM=True, LLM_AUTO_SELECT=False, LLM_HYBRID=False,
                                 LLM_BACKEND="groq", LLM_FALLBACK_BACKEND="", LLM_FIDELITY_FLOOR=0.65,
                                 WHISPER_PROMPT="", WRITING_STYLE="flow"):
            p = tp.TextProcessor()
            out = p.clean(RAW)
        self.assertEqual(out, FAITHFUL)
        self.assertIn("+verbatim", p.last_status)
        self.assertIn("SMALLEST possible changes", prompts[1])

    def test_still_drifting_pastes_basic(self):
        def run(self_, *a, **k):
            return REWRITE
        with mock.patch.object(tp.TextProcessor, "_run_backend", run), \
             mock.patch.multiple(config, CLEAN_WITH_LLM=True, LLM_AUTO_SELECT=False, LLM_HYBRID=False,
                                 LLM_BACKEND="groq", LLM_FALLBACK_BACKEND="", LLM_FIDELITY_FLOOR=0.65,
                                 WHISPER_PROMPT="", WRITING_STYLE="flow"):
            p = tp.TextProcessor()
            out = p.clean(RAW)
        self.assertEqual(p.last_status, "basic:drift")
        self.assertIn("output quality", out)


class RaceManyTests(unittest.TestCase):
    def test_returns_after_grace_without_waiting_for_slow_engine(self):
        fast = lambda: "ok"
        slow = lambda: (time.sleep(3), "late")[1]
        t0 = time.time()
        res = hybrid.race_many([fast, slow], timeout=5, grace=0.2, usable=bool)
        self.assertLess(time.time() - t0, 1.5)
        self.assertEqual(res[0][0], "ok")
        self.assertEqual(res[1], (None, None))


class AutoSelectTests(unittest.TestCase):
    def setUp(self):
        import auto_select
        self.a = auto_select
        self.tmp = tempfile.mkdtemp()
        self._p = mock.patch.object(auto_select, "STATS_FILE", os.path.join(self.tmp, "s.json"))
        self._p.start()
        auto_select._stats = None
        auto_select._models_cache.clear()

    def tearDown(self):
        self._p.stop()
        self.a._stats = None

    def _cfg(self, **kw):
        base = dict(GROQ_API_KEY="g", CEREBRAS_API_KEY="", GEMINI_API_KEY="", OPENAI_API_KEY="",
                    ANTHROPIC_API_KEY="", NVIDIA_API_KEY="", CUSTOM_LLM_URL="", OFFLINE_MODE=False,
                    GROQ_LLM_MODEL="openai/gpt-oss-120b", LLM_AUTO_PROVIDERS=[],
                    OLLAMA_BASE_URL="http://127.0.0.1:11434/v1", OLLAMA_LLM_MODEL="qwen3:4b")
        base.update(kw)
        return mock.patch.multiple(config, **base)

    def test_candidates_use_only_listed_models_and_skip_dead_local(self):
        def get(url, headers=None, timeout=None):
            if "groq" in url:
                return _Resp(body={"data": [{"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3-32b"},
                                            {"id": "whisper-large-v3"}]})
            raise tp.requests.exceptions.ConnectionError("ollama down")
        with self._cfg(), mock.patch.object(self.a.requests, "get", side_effect=get):
            c = self.a.candidates()
        self.assertEqual(c, [("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3-32b")])

    def test_unknown_listing_falls_back_to_configured_model(self):
        with self._cfg(), mock.patch.object(self.a.requests, "get", side_effect=Exception("net")):
            c = self.a.candidates()
        self.assertEqual(c, [("groq", "openai/gpt-oss-120b")])

    def test_best_scoring_result_wins_and_stats_recorded(self):
        cands = [("groq", "m-rewrite"), ("cerebras", "m-faithful"), ("gemini", "m-empty")]
        out = {"m-rewrite": REWRITE, "m-faithful": FAITHFUL, "m-empty": ""}
        def run(self_, sp, raw, prov, allow_stream=True, model=None):
            return out[model]
        with mock.patch.object(self.a, "candidates", return_value=cands), \
             mock.patch.object(tp.TextProcessor, "_run_backend", run), \
             mock.patch.multiple(config, LLM_AUTO_CANDIDATES=3, LLM_AUTO_MAX_WAIT_SEC=3, LLM_AUTO_GRACE_SEC=0.5):
            text, prov, model = self.a.clean("sys", RAW)
        self.assertEqual((text, prov, model), (FAITHFUL, "cerebras", "m-faithful"))
        st = self.a._load_stats()
        self.assertEqual(st["cerebras/m-faithful"]["wins"], 1)
        self.assertEqual(st["gemini/m-empty"]["consec_fail"], 1)
        self.assertTrue(os.path.exists(self.a.STATS_FILE))

    def test_engine_failing_repeatedly_sits_out(self):
        e = self.a._entry("groq/bad")
        e.update(consec_fail=3, last_fail=time.time())
        ranked = self.a._rank([("groq", "bad"), ("groq", "good")], 10)
        self.assertEqual([r[2] for r in ranked], ["good"])

    def test_provider_without_preferred_ids_uses_generic_family(self):
        def get(url, headers=None, timeout=None):
            if "nvidia" in url:
                return _Resp(body={"data": [{"id": "meta/llama-3.2-11b-vision-instruct"},
                                            {"id": "meta/llama-3.1-70b-instruct"}]})
            raise tp.requests.exceptions.ConnectionError("down")
        with self._cfg(GROQ_API_KEY="", NVIDIA_API_KEY="n", LLM_MODEL="nvidia/retired-model"), \
             mock.patch.object(self.a.requests, "get", side_effect=get):
            self.assertEqual(self.a.candidates(), [("nvidia", "meta/llama-3.1-70b-instruct")])

    def test_cooldown_doubles_with_more_failures(self):
        e = self.a._entry("ollama/slow")
        e.update(consec_fail=5, last_fail=time.time() - 1200)   # 20 min ago; cooldown now 40 min
        self.assertEqual(self.a._rank([("ollama", "slow")], 10), [])

    def test_clean_uses_auto_select(self):
        with mock.patch.object(self.a, "clean", return_value=(FAITHFUL, "groq", "x")), \
             mock.patch.multiple(config, CLEAN_WITH_LLM=True, LLM_AUTO_SELECT=True, WHISPER_PROMPT="",
                                 WRITING_STYLE="flow", LLM_FIDELITY_FLOOR=0.65):
            p = tp.TextProcessor()
            self.assertEqual(p.clean(RAW), FAITHFUL)
        self.assertEqual(p.last_status, "auto:groq/x")


class DeepgramKeytermTests(unittest.TestCase):
    def test_keyterms(self):
        import transcriber
        self.assertEqual(transcriber._keyterms("Claude Code, groq\nnibcast"), ["NibCast", "Claude Code", "groq"])


class VoiceIgnoredTests(unittest.TestCase):
    def test_recent_ignored_feed_health(self):
        import state, health
        state._voice_ignored.clear()
        for _ in range(3):
            state.note_voice_ignored("no_wake", "8 word(s)")
        with mock.patch.multiple(config, WAKE_WORD_ENABLED=True, WAKE_WORD="hey nova", WAKE_ENGINE="asr"):
            checks = health.check_wake()
        ids = [c["id"] for c in checks]
        self.assertIn("voice_ignored", ids)
        state._voice_ignored.clear()


if __name__ == "__main__":
    unittest.main()
