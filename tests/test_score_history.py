# ============================================================
#  NibCast — history quality report tests
# ============================================================
#  Guards tools/score_history.py: the detectors must flag the
#  leftovers they exist for (doubled words, repeated phrases,
#  dictionary sound-alikes, skipped LLM cleanup) without flagging
#  clean text, and the DB must be opened read-only.
#
#  Run from project root:   python -m unittest tests.test_score_history
# ============================================================

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import score_history as sh


class DetectorTests(unittest.TestCase):

    def test_doubled_words(self):
        self.assertEqual(sh.doubled_words("an updated updated version"), ["updated"])
        self.assertEqual(sh.doubled_words("I had had enough"), [],
                         "legitimate doubles like 'had had' are not leftovers")

    def test_repeated_phrases(self):
        self.assertIn("give me the", sh.repeated_phrases("so give me the, give me the plan"))
        self.assertEqual(sh.repeated_phrases("a clean sentence with no repeats"), [])

    def test_vocab_misses(self):
        misses = sh.vocab_misses("my Nibcash repo and nip cast", ["NibCast"])
        self.assertIn("Nibcash→NibCast", misses)
        self.assertIn("nip cast→NibCast", misses)
        self.assertEqual(sh.vocab_misses("the NibCast repo", ["NibCast"]), [],
                         "a correctly spelled term next to another word is not a miss")

    def test_basic_cleanup_detection(self):
        self.assertTrue(sh.looks_like_basic_cleanup("so um it works", "So um it works."))
        self.assertFalse(sh.looks_like_basic_cleanup("so um it works", "It works."))

    def test_clean_text_scores_100(self):
        s = sh.score_entry("um it works fine", "It works fine.", [])
        self.assertEqual(s["score"], 100)


class ReadOnlyDbTests(unittest.TestCase):

    def test_load_db_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "history.db")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE transcriptions (id INTEGER PRIMARY KEY, created_at TEXT,"
                           " raw_text TEXT, clean_text TEXT, status TEXT, target_app TEXT)")
                db.execute("INSERT INTO transcriptions VALUES (1,'2026-01-01','a a b','A a b.','success','x')")
            db.close()
            before = os.path.getmtime(path)
            rows = sh.load_db(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(os.path.getmtime(path), before)


if __name__ == "__main__":
    unittest.main()
