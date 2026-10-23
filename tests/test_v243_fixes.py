# ============================================================
#  NibCast — v2.4.3 regression tests
# ============================================================
#  Covers the fixes driven by real usage history:
#    • rate-limited (429) cleanup is retried instead of silently
#      pasting the raw transcript, and the outcome is recorded
#    • stutters / doubled words / repeated phrases are collapsed
#    • the chatbot guard no longer discards the speaker's own words
#    • wake-phrase mishear suggestions from discarded history
#    • Privacy Mode is enforced, the dashboard refuses foreign
#      hosts / origins, and backups / debug bundles leak nothing
#
#  Run from project root:   python -m unittest tests.test_v243_fixes
# ============================================================

import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database as db
import text_processor as tp
import transcriber


class _Resp:
    def __init__(self, status, content="", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class RepeatFilterTests(unittest.TestCase):

    def test_collapses_doubles_and_phrases(self):
        f = tp._fix_duplicates
        self.assertEqual(f("an updated updated version with new new repos"),
                         "an updated version with new repos")
        self.assertEqual(f("local LLM models local LLM models and"), "local LLM models and")
        self.assertEqual(f("give me the, give me the detailed plan"), "give me the detailed plan")
        self.assertEqual(f("whenever I I would"), "whenever I would")
        self.assertEqual(f("it's, it's, it's, it's done"), "it's done")

    def test_keeps_intentional_repeats(self):
        f = tp._fix_duplicates
        self.assertEqual(f("I had had enough"), "I had had enough")
        self.assertEqual(f("very very good"), "very very good")
        self.assertEqual(f("Good. Good. Okay."), "Good. Good. Okay.")
        self.assertEqual(f("call 911 911"), "call 911 911")

    def test_basic_cleanup_also_dedups(self):
        self.assertEqual(tp.TextProcessor._basic_cleanup("the the plan"), "The plan.")


class GuardTests(unittest.TestCase):

    def test_dictated_openers_are_not_chatbot_replies(self):
        for raw in ("it looks like the build failed again",
                    "based on the logs the wake word fails",
                    "here is the plan for tomorrow",
                    "sure, I can do that tomorrow"):
            self.assertFalse(tp._is_llm_mode_hallucination(raw, raw.capitalize() + "."), raw)

    def test_real_chatbot_reply_still_caught(self):
        self.assertTrue(tp._is_llm_mode_hallucination(
            "can you hear me", "Sure! How can I help you today?"))


class CleanupStatusTests(unittest.TestCase):

    def setUp(self):
        # Every config value the cleanup path reads must be pinned here. These
        # tests used to inherit the developer's own ~/.nibcast/config.json, so
        # Offline Mode or Hybrid being on at home made them fail for no reason.
        self._saved = {k: getattr(config, k) for k in
                       ("CLEAN_WITH_LLM", "LLM_BACKEND", "GROQ_API_KEY", "BRAIN_MODE",
                        "LLM_FALLBACK_BACKEND", "HTTP_RETRIES", "WHISPER_PROMPT", "PRIVACY_MODE",
                        "OFFLINE_MODE", "LLM_HYBRID", "LLM_BRAIN_SECONDARY")}
        config.CLEAN_WITH_LLM = True
        config.LLM_BACKEND = "groq"
        config.GROQ_API_KEY = "test"
        config.BRAIN_MODE = False
        config.LLM_FALLBACK_BACKEND = ""
        config.HTTP_RETRIES = 3
        config.WHISPER_PROMPT = ""
        config.PRIVACY_MODE = False
        config.OFFLINE_MODE = False
        config.LLM_HYBRID = False
        config.LLM_BRAIN_SECONDARY = ""
        self.sleep = mock.patch.object(tp.time, "sleep").start()

    def tearDown(self):
        mock.patch.stopall()
        for k, v in self._saved.items():
            setattr(config, k, v)

    def test_429_is_retried_with_retry_after(self):
        responses = [_Resp(429, headers={"retry-after": "2"}), _Resp(200, "Fixed text.")]
        with mock.patch.object(tp.requests, "post", side_effect=responses):
            p = tp.TextProcessor()
            out = p.clean("fixed text um")
        self.assertEqual(out, "Fixed text.")
        self.assertEqual(p.last_status, "llm")
        self.sleep.assert_any_call(2.0)

    def test_persistent_429_is_recorded(self):
        with mock.patch.object(tp.requests, "post", return_value=_Resp(429)):
            p = tp.TextProcessor()
            out = p.clean("hello world")
        self.assertEqual(out, "Hello world.")
        self.assertEqual(p.last_status, "basic:rate_limited")

    def test_guard_rejection_tries_fallback(self):
        config.LLM_FALLBACK_BACKEND = "ollama"
        responses = [_Resp(200, "Sure! How can I help you today?"), _Resp(200, "Can you hear me?")]
        with mock.patch.object(tp.requests, "post", side_effect=responses):
            p = tp.TextProcessor()
            out = p.clean("can you hear me")
        self.assertEqual(out, "Can you hear me?")
        self.assertEqual(p.last_status, "fallback:ollama")

    def test_llm_off(self):
        config.CLEAN_WITH_LLM = False
        p = tp.TextProcessor()
        p.clean("hi there")
        self.assertEqual(p.last_status, "off")

    def test_retry_after_parsing(self):
        self.assertEqual(tp._retry_after({"retry-after": "3.5"}), 3.5)
        self.assertIsNone(tp._retry_after({"retry-after": "Wed, 21 Oct"}))
        self.assertIsNone(tp._retry_after({}))


class HallucinationTests(unittest.TestCase):

    def test_youtube_outro_in_other_languages(self):
        self.assertTrue(transcriber._is_hallucination(
            "Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn"))
        self.assertFalse(transcriber._is_hallucination("please subscribe to the newsletter"))


class WakeSuggestionTests(unittest.TestCase):

    def test_ranks_repeated_mishears(self):
        texts = (["Noba."] * 40 + ["Nova"] * 30 + ["Hey Noba"] * 20 + ["Nover"] * 10 +
                 ["Hello?"] * 28 + ["Bye"] * 26 + ["Hey"] * 3 + ["the"] * 14 + ["hark"] * 10)
        got = [s["text"] for s in db.rank_wake_alternatives(texts, "hey nova")]
        self.assertEqual(got[:4], ["noba", "nova", "hey noba", "nover"])
        for noise in ("hello", "bye", "hey", "the"):
            self.assertNotIn(noise, got)

    def test_skips_existing_and_rare(self):
        texts = ["Noba"] * 5 + ["Naval"] * 2
        got = [s["text"] for s in db.rank_wake_alternatives(texts, "hey nova", existing=["noba"])]
        self.assertEqual(got, [])


class PrivacyModeTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = mock.patch.object(db, "DB_PATH", os.path.join(self._tmp.name, "h.db"))
        self._db.start()
        db.init_db()
        self._priv = config.PRIVACY_MODE

    def tearDown(self):
        config.PRIVACY_MODE = self._priv
        self._db.stop()
        self._tmp.cleanup()

    def _last(self):
        return db._get_all_rows(1)[0]

    def test_text_not_stored_in_privacy_mode(self):
        config.PRIVACY_MODE = True
        db.save_transcription("secret raw", "Secret clean.", llm_status="llm")
        row = self._last()
        self.assertEqual((row["raw_text"], row["clean_text"]), ("", ""))
        self.assertEqual(row["word_count"], 2, "usage stats still count words")
        self.assertEqual(row["llm_status"], "llm")

    def test_text_stored_normally(self):
        config.PRIVACY_MODE = False
        db.save_transcription("raw", "Clean.", llm_status="basic:timeout")
        row = self._last()
        self.assertEqual(row["clean_text"], "Clean.")
        self.assertEqual(row["llm_status"], "basic:timeout")

    def test_log_redaction(self):
        from logger import redact
        config.PRIVACY_MODE = True
        self.assertNotIn("secret", redact("my secret"))
        config.PRIVACY_MODE = False
        self.assertEqual(redact("my secret"), "'my secret'")


class DashboardSecurityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import web_dashboard
        cls.wd = web_dashboard

    def setUp(self):
        self.client = self.wd.app.test_client()

    def test_foreign_host_refused(self):
        r = self.client.get("/login", headers={"Host": "evil.example:7171"})
        self.assertEqual(r.status_code, 403)

    def test_cross_origin_post_refused(self):
        r = self.client.post("/api/inject", json={"text": "rm -rf"},
                             headers={"Origin": "http://localhost:3000"})
        self.assertEqual(r.status_code, 403)

    def test_own_origin_allowed(self):
        r = self.client.post("/api/logout", headers={"Host": "localhost:7171",
                                                     "Origin": "http://localhost:7171"})
        self.assertEqual(r.status_code, 200)

    def test_debug_log_scrub(self):
        s = self.wd._scrub_log_line
        self.assertNotIn("plan", s("t [INFO] ✅ [Groq Whisper] 'my secret plan'\n"))
        self.assertNotIn("hello", s("t [INFO] 📝 Pasting: 'hello' → Chrome\n"))
        self.assertEqual(s("t [INFO] ✅ Database ready\n"), "t [INFO] ✅ Database ready\n")

    def test_backup_excludes_auth_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            for name in (".vf_secret", ".vf_totp", "config.json"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            with mock.patch.object(config, "USER_DIR", d), \
                 mock.patch.object(self.wd.auth, "is_configured", return_value=True):
                with self.client.session_transaction() as sess:
                    sess["vf_auth"] = True
                r = self.client.get("/api/backup?db=0&logs=0")
            names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
        self.assertIn("config.json", names)
        self.assertNotIn(".vf_secret", names)
        self.assertNotIn(".vf_totp", names)


if __name__ == "__main__":
    unittest.main()
