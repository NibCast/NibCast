# ============================================================
#  NibCast — System Health tests
# ============================================================
#  The health panel must explain every common failure in plain
#  words and offer a fix that actually changes the setting. No
#  network: provider probes are mocked.
#
#  Run from project root:   python -m unittest tests.test_health
# ============================================================

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import health
import local_asr
import local_models


def _cfg(**kw):
    return mock.patch.multiple(config, **kw)


def _status(checks, cid):
    return next(c for c in checks if c["id"] == cid)


class ExplainTests(unittest.TestCase):

    def test_reasons_in_plain_words(self):
        self.assertIn("rate-limiting", health.explain_llm_status("basic:rate_limited"))
        self.assertIn("HTTP 401", health.explain_llm_status("basic:http_401"))
        self.assertEqual(health.explain_llm_status("llm"), "")
        self.assertEqual(health.explain_llm_status(""), "")


class AsrChecks(unittest.TestCase):

    def setUp(self):
        health.clear_cache()

    def test_missing_groq_key_offers_offline(self):
        with _cfg(ASR_BACKEND="groq", GROQ_API_KEY="", OFFLINE_MODE=False):
            c = health.check_asr()
        self.assertEqual(c["status"], "error")
        self.assertIn("use_offline", [f["action"] for f in c["fixes"]])

    def test_bad_key_is_named(self):
        with _cfg(ASR_BACKEND="groq", GROQ_API_KEY="gsk_bad123456", OFFLINE_MODE=False), \
             mock.patch.object(health, "_http_status", return_value=("auth", "HTTP 401")):
            c = health.check_asr()
        self.assertEqual(c["status"], "error")
        self.assertIn("rejected your API key", c["title"])

    def test_no_internet_is_named(self):
        with _cfg(ASR_BACKEND="groq", GROQ_API_KEY="gsk_x12345678", OFFLINE_MODE=False), \
             mock.patch.object(health, "_http_status", return_value=("down", "ConnectionError")):
            c = health.check_asr()
        self.assertIn("Can't reach", c["title"])

    def test_offline_mode_with_cloud_asr_conflict(self):
        with _cfg(ASR_BACKEND="groq", GROQ_API_KEY="k", OFFLINE_MODE=True):
            c = health.check_asr()
        self.assertEqual(c["status"], "error")
        self.assertEqual({f["action"] for f in c["fixes"]}, {"use_offline", "offline_off"})

    def test_in_app_whisper_not_installed(self):
        with _cfg(ASR_BACKEND="faster_whisper"), \
             mock.patch.object(local_asr, "status", return_value={"available": False, "state": "unavailable"}):
            c = health.check_asr()
        self.assertIn("not installed", c["title"])


class LlmChecks(unittest.TestCase):

    def setUp(self):
        health.clear_cache()

    def test_no_fallback_is_a_warning_with_fix(self):
        with _cfg(CLEAN_WITH_LLM=True, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_FALLBACK_BACKEND="",
                  LLM_HYBRID=False, OFFLINE_MODE=False), \
             mock.patch.object(health, "_http_status", return_value=("ok", "")):
            checks = health.check_llms()
        fb = _status(checks, "llm_fallback")
        self.assertEqual(fb["status"], "warn")
        self.assertEqual(fb["fixes"][0]["action"], "fallback_local")

    def test_local_server_not_running(self):
        with _cfg(CLEAN_WITH_LLM=True, LLM_BACKEND="ollama", OLLAMA_BASE_URL="http://localhost:11434/v1",
                  OLLAMA_LLM_MODEL="qwen2.5:7b", LLM_FALLBACK_BACKEND="", LLM_HYBRID=False), \
             mock.patch.object(local_models, "detect", return_value=[]):
            c = _status(health.check_llms(), "llm_primary")
        self.assertIn("isn't running", c["title"])

    def test_local_model_missing(self):
        servers = [{"name": "Ollama", "port": 11434, "base_url": "http://127.0.0.1:11434/v1",
                    "models": ["llama3.2"]}]
        with _cfg(CLEAN_WITH_LLM=True, LLM_BACKEND="ollama", OLLAMA_BASE_URL="http://localhost:11434/v1",
                  OLLAMA_LLM_MODEL="qwen2.5:7b", LLM_FALLBACK_BACKEND="", LLM_HYBRID=False), \
             mock.patch.object(local_models, "detect", return_value=servers):
            c = _status(health.check_llms(), "llm_primary")
        self.assertIn("doesn't have 'qwen2.5:7b'", c["title"])
        self.assertIn("llama3.2", c["detail"])


class RecentDictationTests(unittest.TestCase):

    def test_reports_skipped_cleanups_and_why(self):
        import database as db
        with tempfile.TemporaryDirectory() as d, mock.patch.object(db, "DB_PATH", os.path.join(d, "h.db")), \
             _cfg(PRIVACY_MODE=False):
            db.init_db()
            for st in ("basic:rate_limited", "basic:rate_limited", "llm"):
                db.save_transcription("raw", "Clean.", llm_status=st)
            c = health.check_recent_dictations()
        self.assertEqual(c["status"], "error")
        self.assertIn("2 of the last 3", c["title"])
        self.assertIn("rate-limiting", c["detail"])


class RunAllTests(unittest.TestCase):

    def test_overall_and_count(self):
        with _cfg(ASR_BACKEND="groq", GROQ_API_KEY="", OFFLINE_MODE=False, CLEAN_WITH_LLM=False,
                  WAKE_WORD_ENABLED=False), \
             mock.patch.object(health, "check_recent_dictations",
                               return_value=health._check("recent", "Recent", "ok", "fine")), \
             mock.patch.object(health, "check_offline_readiness",
                               return_value=health._check("offline", "Offline", "info", "n/a")):
            r = health.run_all(mic_level_at=time.time(), version_stale=True)
        self.assertEqual(r["overall"], "error")
        ids = [c["id"] for c in r["checks"]]
        self.assertIn("stale", ids)
        self.assertGreaterEqual(r["errors"], 2)        # stale + missing key

    def test_mic_idle_is_fine_in_hotkey_mode(self):
        with _cfg(WAKE_WORD_ENABLED=False):
            self.assertEqual(health.check_microphone(0.0)["status"], "ok")

    def test_silent_mic_is_an_error_when_voice_activation_streams(self):
        with _cfg(WAKE_WORD_ENABLED=True):
            self.assertEqual(health.check_microphone(0.0)["status"], "error")
            self.assertEqual(health.check_microphone(time.time())["status"], "ok")

    def test_recent_mic_failure_is_reported(self):
        from logger import RECENT_PROBLEMS
        RECENT_PROBLEMS.append({"time": time.time(), "level": "ERROR",
                                "message": "❌ Failed to open input device: no device"})
        try:
            with _cfg(WAKE_WORD_ENABLED=False):
                c = health.check_microphone(time.time())
            self.assertEqual(c["status"], "error")
            self.assertIn("Failed to open input device", c["detail"])
        finally:
            RECENT_PROBLEMS.pop()


class FixTests(unittest.TestCase):

    def setUp(self):
        self._save = mock.patch.object(config, "save")
        self._save.start()

    def tearDown(self):
        self._save.stop()

    def test_go_online_needs_key(self):
        with _cfg(GROQ_API_KEY=""):
            r = health.apply_fix("use_online")
        self.assertFalse(r["ok"])
        self.assertIn("Groq API key", r["message"])

    def test_go_online(self):
        with _cfg(GROQ_API_KEY="k", OFFLINE_MODE=True, ASR_BACKEND="faster_whisper", LLM_BACKEND="ollama"):
            r = health.apply_fix("use_online")
            self.assertTrue(r["ok"])
            self.assertEqual((config.ASR_BACKEND, config.LLM_BACKEND, config.OFFLINE_MODE), ("groq", "groq", False))

    def test_go_offline_says_what_is_missing(self):
        with _cfg(OFFLINE_MODE=False, ASR_BACKEND="groq", LLM_BACKEND="groq"), \
             mock.patch.object(local_asr, "available", return_value=False), \
             mock.patch.object(local_models, "detect", return_value=[]):
            r = health.apply_fix("use_offline")
            self.assertFalse(r["ok"])
            self.assertIn("faster-whisper", r["message"])
            self.assertIn("local model server", r["message"])
            self.assertFalse(config.OFFLINE_MODE, "never block cloud before offline actually works")

    def test_go_offline_switches_everything(self):
        servers = [{"name": "Ollama", "port": 11434, "base_url": "http://127.0.0.1:11434/v1", "models": ["qwen2.5:7b"]}]
        with _cfg(OFFLINE_MODE=False, ASR_BACKEND="groq", LLM_BACKEND="groq", OLLAMA_BASE_URL="", OLLAMA_LLM_MODEL=""), \
             mock.patch.object(local_asr, "available", return_value=True), \
             mock.patch.object(local_asr, "load_async"), \
             mock.patch.object(local_models, "detect", return_value=servers), \
             mock.patch.object(local_models, "warm_up_async"):
            r = health.apply_fix("use_offline")
            self.assertTrue(r["ok"], r["message"])
            self.assertEqual((config.ASR_BACKEND, config.LLM_BACKEND, config.OFFLINE_MODE),
                             ("faster_whisper", "ollama", True))

    def test_local_fallback(self):
        servers = [{"name": "LM Studio", "port": 1234, "base_url": "http://127.0.0.1:1234/v1", "models": ["qwen"]}]
        with _cfg(LLM_BACKEND="groq", LLM_FALLBACK_BACKEND="", OLLAMA_BASE_URL="", OLLAMA_LLM_MODEL=""), \
             mock.patch.object(local_models, "detect", return_value=servers), \
             mock.patch.object(local_models, "warm_up_async"):
            r = health.apply_fix("fallback_local")
            self.assertTrue(r["ok"])
            self.assertEqual(config.LLM_FALLBACK_BACKEND, "ollama")

    def test_fallback_never_swaps_a_local_primary_model(self):
        servers = [{"name": "Ollama", "port": 11434, "base_url": "http://127.0.0.1:11434/v1", "models": ["llama3.2"]}]
        with _cfg(LLM_BACKEND="ollama", OLLAMA_LLM_MODEL="qwen2.5:7b", LLM_FALLBACK_BACKEND=""), \
             mock.patch.object(local_models, "detect", return_value=servers):
            r = health.apply_fix("fallback_local")
            self.assertTrue(r["ok"])
            self.assertEqual(config.OLLAMA_LLM_MODEL, "qwen2.5:7b")

    def test_unknown_action(self):
        self.assertFalse(health.apply_fix("rm -rf")["ok"])


class UrlMatchTests(unittest.TestCase):

    def test_local_url_spellings_match(self):
        k = health._server_key
        self.assertEqual(k("http://localhost:11434"), k("http://127.0.0.1:11434/v1/"))
        self.assertEqual(k("http://[::1]:11434/v1"), k("http://127.0.0.1:11434/v1"))
        self.assertNotEqual(k("http://127.0.0.1:1234/v1"), k("http://127.0.0.1:11434/v1"))


class HybridRowTests(unittest.TestCase):

    def test_no_hybrid_row_when_secondary_equals_primary(self):
        with _cfg(CLEAN_WITH_LLM=True, LLM_BACKEND="groq", GROQ_API_KEY="k", LLM_FALLBACK_BACKEND="",
                  LLM_HYBRID=True, LLM_BRAIN_SECONDARY="groq", OFFLINE_MODE=False), \
             mock.patch.object(health, "_http_status", return_value=("ok", "")):
            ids = [c["id"] for c in health.check_llms()]
        self.assertNotIn("llm_hybrid", ids)


class LoggerBufferTests(unittest.TestCase):

    def test_warnings_are_kept_for_the_panel(self):
        from logger import log, RECENT_PROBLEMS
        log.warning("health-test-marker")
        self.assertTrue(any(p["message"] == "health-test-marker" for p in RECENT_PROBLEMS))


class EndpointTests(unittest.TestCase):

    def test_health_endpoint(self):
        import web_dashboard
        c = web_dashboard.app.test_client()
        with mock.patch.object(web_dashboard.auth, "is_configured", return_value=True), \
             mock.patch.object(health, "run_all", return_value={"overall": "ok", "problems": 0,
                                                                "checks": [], "recent_log": []}):
            with c.session_transaction() as s:
                s["vf_auth"] = True
            d = c.get("/api/health").get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["overall"], "ok")


if __name__ == "__main__":
    unittest.main()
