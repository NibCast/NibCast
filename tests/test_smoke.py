# ============================================================
#  NibCast — smoke tests
# ============================================================
#  Run from project root:   python -m unittest tests.test_smoke
# ============================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBasicCleanup(unittest.TestCase):
    def test_capitalizes_and_periods(self):
        from text_processor import TextProcessor
        self.assertEqual(TextProcessor._basic_cleanup("hello world"), "Hello world.")
        self.assertEqual(TextProcessor._basic_cleanup(""), "")
        self.assertEqual(TextProcessor._basic_cleanup("Already capitalized."),
                         "Already capitalized.")
        self.assertEqual(TextProcessor._basic_cleanup("question?"),
                         "Question?")


class TestParseCombo(unittest.TestCase):
    def test_combos(self):
        from hotkey_listener import _parse_combo_tokens
        self.assertEqual(_parse_combo_tokens("<ctrl>+<shift>+<space>"),
                         frozenset({"ctrl", "shift", "space"}))
        self.assertEqual(_parse_combo_tokens("<f9>"), frozenset({"f9"}))
        self.assertEqual(_parse_combo_tokens("<ctrl>+a"), frozenset({"ctrl", "a"}))


class TestConfigPersistence(unittest.TestCase):
    def test_round_trip(self):
        import tempfile, json
        os.environ.pop("NibCast_NVIDIA_API_KEY", None)
        td = tempfile.mkdtemp(prefix="vf_test_")
        cf = os.path.join(td, "config.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"LANGUAGE": "fr", "HTTP_RETRIES": 7}, f)

        import config
        orig_cf       = config.CONFIG_FILE
        orig_lang     = config.LANGUAGE
        orig_retries  = config.HTTP_RETRIES
        try:
            config.CONFIG_FILE = cf
            config.load()
            self.assertEqual(config.LANGUAGE, "fr")
            self.assertEqual(config.HTTP_RETRIES, 7)
        finally:
            # Restore everything so we don't poison sibling tests.
            config.CONFIG_FILE  = orig_cf
            config.LANGUAGE     = orig_lang
            config.HTTP_RETRIES = orig_retries


class TestTargetManager(unittest.TestCase):
    def test_no_false_positive_for_code(self):
        # "Discord" should not match the "code" rule even though
        # the keyword list contains 'code' (substring match would have
        # falsely caught this).
        import target_manager as tm

        tm.set_override("")
        orig = tm._active_window_name
        try:
            tm._active_window_name = lambda: "discord — #general"
            info = tm.detect_target()
            self.assertEqual(info["category"], "chat")
        finally:
            tm._active_window_name = orig


class TestDatabaseRoundTrip(unittest.TestCase):
    """Save → query → delete via a temp DB."""

    def test_round_trip(self):
        import tempfile
        td = tempfile.mkdtemp(prefix="vf_db_test_")
        import database
        orig_path = database.DB_PATH
        database.DB_PATH = os.path.join(td, "test.db")
        try:
            database.init_db()
            database.save_transcription(
                raw_text="hello world", clean_text="Hello world.",
                duration_sec=1.5, target_app="Test", category="general",
                language="en", status="success",
            )
            rows = database.get_history(limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["clean_text"], "Hello world.")
            self.assertEqual(rows[0]["target_app"], "Test")
            self.assertEqual(rows[0]["word_count"], 2)

            stats = database.get_stats()
            self.assertEqual(stats["total_sessions"], 1)

            database.delete_transcription(rows[0]["id"])
            self.assertEqual(len(database.get_history(limit=10)), 0)
        finally:
            database.DB_PATH = orig_path


class TestAuthLegacyMigration(unittest.TestCase):
    """Legacy SHA-256 hash should still verify, then get rewritten with PBKDF2."""

    def test_legacy_pin_still_verifies_and_upgrades(self):
        import tempfile, hashlib
        td = tempfile.mkdtemp(prefix="vf_auth_test_")
        import auth
        orig_auth = auth._AUTH_FILE
        orig_salt = auth._SALT_FILE
        try:
            auth._AUTH_FILE = os.path.join(td, ".vf_auth")
            auth._SALT_FILE = os.path.join(td, ".vf_salt")
            # Plant a legacy hash (single sha256 with the v1 static salt).
            pin = "test1234"
            legacy = hashlib.sha256(f"nibcast_v1:{pin}".encode()).hexdigest()
            with open(auth._AUTH_FILE, "w", encoding="utf-8") as f:
                f.write(legacy)

            # First verify: should accept legacy AND rewrite the file with PBKDF2.
            self.assertTrue(auth.verify_pin(pin))
            with open(auth._AUTH_FILE, "r", encoding="utf-8") as f:
                upgraded = f.read().strip()
            self.assertNotEqual(upgraded, legacy,
                                "Legacy hash should have been upgraded to PBKDF2")

            # Second verify: PBKDF2 path.
            self.assertTrue(auth.verify_pin(pin))
            # Wrong PIN still rejected.
            self.assertFalse(auth.verify_pin("nope"))
        finally:
            auth._AUTH_FILE = orig_auth
            auth._SALT_FILE = orig_salt


if __name__ == "__main__":
    unittest.main()
