# ============================================================
#  NibCast — v2.9.0 learned-vocabulary tests
# ============================================================
#  • which words are learned (and which never are)
#  • corrections keep the misheard form, filtered for safety
#  • manual add / pin / remove-for-good / Wispr import
#  • feeding: Whisper prompt, Deepgram keyterms, LLM hint
#  • Brain-mode ASR judging no longer picks the garbled run-on
#
#  Run from project root:   python -m unittest tests.test_v290_vocabulary
# ============================================================

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
import text_processor as tp
import transcriber
import vocabulary as v


class _TempDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = mock.patch.object(database, "DB_PATH", os.path.join(self.tmp, "h.db"))
        self._p.start()
        database.init_db()
        self._c = mock.patch.multiple(config, VOCAB_AUTO_LEARN=True, PRIVACY_MODE=False,
                                      VOCAB_MAX_TERMS=50, VOCAB_FORGET_DAYS=90)
        self._c.start()
        v._invalidate()

    def tearDown(self):
        self._c.stop()
        self._p.stop()
        v._invalidate()

    def statuses(self):
        return {w["term"]: w["status"] for w in v.list_all()}


class ExtractionTests(unittest.TestCase):
    def test_shapes_names_and_phrases(self):
        c = v.candidates_in("Push the README to GitHub. Then ask Claude Code about NibCast "
                            "and the Play Store with LLMs. And Maybe later.")
        for k in ("readme", "github", "nibcast", "claude code", "play store", "llms"):
            self.assertIn(k, c)
        # sentence starts and ordinary words are not names
        for k in ("push", "then", "and", "maybe", "the"):
            self.assertNotIn(k, c)

    def test_corrections(self):
        self.assertEqual(
            v.corrections_in("push it to the git hub and the new cast app, read me this file",
                             "Push it to GitHub, and the NibCast app, README file."),
            [("git hub", "GitHub"), ("new cast", "NibCast"), ("read me", "README")])
        self.assertEqual(v.corrections_in("check github now", "Check GitHub now."), [("github", "GitHub")])

    def test_grammar_fixes_are_not_corrections(self):
        self.assertEqual(v.corrections_in("most of them has low score", "Most of them have a low score."), [])

    def test_unsafe_misheard_forms_rejected(self):
        self.assertFalse(v._useful_misheard("githubs", "GitHub"))   # plural
        self.assertFalse(v._useful_misheard("you I", "UI"))         # ordinary words
        self.assertFalse(v._useful_misheard("cast", "NibCast"))     # one common-looking word
        self.assertTrue(v._useful_misheard("nip cache", "NibCast"))


class LearningTests(_TempDB):
    def test_needs_several_dictations(self):
        v.learn("", "I pushed it to GitHub.")
        v.learn("", "The GitHub repo is public.")
        self.assertNotIn("GitHub", v.active_terms())
        v.learn("", "Open GitHub again.")
        v._invalidate()
        self.assertIn("GitHub", v.active_terms())

    def test_plain_capitalised_word_needs_consistency(self):
        for _ in range(4):
            v.learn("", "I asked Deidre about it.")
        v._invalidate()
        self.assertIn("Deidre", v.active_terms())
        for _ in range(4):
            v.learn("", "Mark it as done, Mark.")          # "Mark" … and "mark"
        for _ in range(4):
            v.learn("", "please mark the task done")
        v._invalidate()
        self.assertNotIn("Mark", v.active_terms())

    def test_corrections_learn_misheard_form(self):
        v.learn("the new cast app", "The NibCast app.")
        v.learn("open new cast again", "Open NibCast again.")
        v._invalidate()
        self.assertIn("NibCast", v.active_terms())
        self.assertIn(("new cast", "NibCast"), v.known_mishearings())

    def test_removed_word_never_comes_back(self):
        for _ in range(3):
            v.learn("", "Ask GitHub.")
        v.set_status("GitHub", "blocked")
        for _ in range(5):
            v.learn("", "Ask GitHub again.")
        v._invalidate()
        self.assertNotIn("GitHub", v.active_terms())
        self.assertEqual(self.statuses()["GitHub"], "blocked")

    def test_manual_add_pin_and_keyterm_rules(self):
        v.add("PC")                       # short, but yours → boosted
        for _ in range(3):
            v.learn("", "Use the AI tool and the API.")
        v._invalidate()
        self.assertIn("PC", v.keyterms())
        self.assertNotIn("AI", v.keyterms())       # short and automatic → prompt only
        self.assertIn("API", v.keyterms())
        self.assertIn("AI", v.active_terms())
        v.set_status("API", "pinned")
        v._invalidate()
        self.assertEqual(v.active_terms()[0], "API")

    def test_privacy_mode_learns_nothing(self):
        with mock.patch.object(config, "PRIVACY_MODE", True):
            for _ in range(3):
                v.learn("", "Ask GitHub.")
        self.assertEqual(v.list_all(), [])

    def test_backfill_runs_once(self):
        for _ in range(3):
            database.save_transcription(raw_text="x", clean_text="I use CapCut daily.")
        self.assertEqual(v.backfill(), 3)
        self.assertEqual(v.backfill(), 0)
        v._invalidate()
        self.assertIn("CapCut", v.active_terms())

    def test_wispr_import_filters_private_and_expansions(self):
        w = os.path.join(self.tmp, "flow.sqlite")
        c = sqlite3.connect(w)
        c.execute("CREATE TABLE Dictionary (phrase TEXT, replacement TEXT, isDeleted INT, isSnippet INT)")
        c.executemany("INSERT INTO Dictionary VALUES (?,?,?,?)", [
            ("Wispr Flow", None, 0, 0), ("someone@example.com", None, 0, 0),
            ("btw", "by the way", 0, 0), ("my link", "https://x.y/z", 0, 1),
            ("Old Word", None, 1, 0), ("Kubernetes", None, 0, 0)])
        c.commit(); c.close()
        self.assertEqual(v.import_wispr(w), 2)
        v._invalidate()
        self.assertEqual(sorted(v.active_terms()), ["Kubernetes", "Wispr Flow"])


class FeedingTests(_TempDB):
    def test_whisper_prompt_is_a_sentence_with_user_terms_last(self):
        v.add("GitHub")
        p = v.whisper_prompt(user_terms=["NibCast", "Claude Code"])
        self.assertEqual(p, "This dictation may mention GitHub, NibCast and Claude Code.")

    def test_deepgram_keyterms_include_learned(self):
        v.add("CapCut")
        self.assertEqual(transcriber._keyterms("Groq", learned=v.keyterms()), ["NibCast", "Groq", "CapCut"])

    def test_llm_gets_learned_words_and_mishearings(self):
        v.add("NibCast", misheard="nip cache")
        seen = {}
        def run(self_, system_prompt, raw_text, backend_name, allow_stream=True, model=None):
            seen["p"] = system_prompt
            return "Open NibCast now please."
        with mock.patch.object(tp.TextProcessor, "_run_backend", run), \
             mock.patch.multiple(config, CLEAN_WITH_LLM=True, LLM_AUTO_SELECT=False, LLM_HYBRID=False,
                                 LLM_BACKEND="groq", LLM_FALLBACK_BACKEND="", WHISPER_PROMPT="",
                                 WRITING_STYLE="flow", LLM_FIDELITY_FLOOR=0.65):
            tp.TextProcessor().clean("open nip cache now please")
        self.assertIn("NibCast", seen["p"])
        self.assertIn('"nip cache" → NibCast', seen["p"])


class BrainJudgingTests(unittest.TestCase):
    def test_punctuated_transcript_beats_longer_run_on(self):
        run_on = ("just the best we can do or maybe to get create a new branch and do all just use a "
                  "pull request to add a new features right along from all the resources what about "
                  "the course I'm saying the corrections for crazy toll feed the list anyway")
        clean = ("Just the best we can do, or maybe create a new branch and just use a pull request "
                 "to add the new features. Try to learn from all the resources. Feed the list anyway.")
        self.assertGreater(transcriber._transcript_quality(clean, run_on),
                           transcriber._transcript_quality(run_on, clean))

    def test_truncated_transcript_loses(self):
        full = "We need to ship the release today. Then write the notes and tag it on GitHub."
        cut = "We need to ship."
        self.assertGreater(transcriber._transcript_quality(full, cut),
                           transcriber._transcript_quality(cut, full))


class ClaudeKeyTests(unittest.TestCase):
    """v2.9.1: adding an Anthropic (Claude) key actually works end to end."""

    def test_retired_claude_model_is_migrated_on_load(self):
        import json
        tmp = tempfile.mkdtemp()
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w") as f:
            json.dump({"ANTHROPIC_LLM_MODEL": "claude-3-5-haiku-20241022"}, f)
        with mock.patch.object(config, "CONFIG_FILE", cfg), \
             mock.patch.object(config, "ANTHROPIC_LLM_MODEL", "x"):
            config.load()
            self.assertEqual(config.ANTHROPIC_LLM_MODEL, "claude-haiku-5-5")

    def test_auto_select_lists_anthropic_models(self):
        import auto_select as a
        a._models_cache.clear()
        class R:
            status_code = 200
            def json(self):
                return {"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-5-5-20260601"}]}
        def get(url, headers=None, timeout=None):
            if "anthropic" in url:
                self.assertIn("x-api-key", headers)
                return R()
            raise Exception("down")
        with mock.patch.multiple(config, GROQ_API_KEY="", CEREBRAS_API_KEY="", GEMINI_API_KEY="",
                                 OPENAI_API_KEY="", NVIDIA_API_KEY="", CUSTOM_LLM_URL="",
                                 ANTHROPIC_API_KEY="sk-ant-x", LLM_AUTO_PROVIDERS=[], OFFLINE_MODE=False), \
             mock.patch.object(a.requests, "get", side_effect=get):
            self.assertEqual(a.candidates(), [("anthropic", "claude-haiku-5-5-20260601"),
                                              ("anthropic", "claude-sonnet-5-5")])
        a._models_cache.clear()

    def test_test_llm_endpoint_tests_the_requested_provider(self):
        import web_dashboard
        called = []
        def run(self_, system_prompt, raw_text, backend_name, allow_stream=True, model=None):
            called.append(backend_name)
            return "I was trying to check whether the Groq API is working properly."
        c = web_dashboard.app.test_client()
        with mock.patch.object(web_dashboard.auth, "is_configured", return_value=True), \
             mock.patch.object(tp.TextProcessor, "_run_backend", run), \
             mock.patch.multiple(config, ANTHROPIC_API_KEY="sk-ant-x", LLM_AUTO_SELECT=True):
            with c.session_transaction() as s:
                s["vf_auth"] = True
            d = c.post("/api/test-llm", json={"backend": "anthropic"}).get_json()
        self.assertTrue(d["ok"], d)
        self.assertEqual(called, ["anthropic"])


if __name__ == "__main__":
    unittest.main()
