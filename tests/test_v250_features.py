# ============================================================
#  NibCast — v2.5.0 feature tests
# ============================================================
#  • Double-tap and mouse-button triggers (no key chords)
#  • Local LLM discovery (Ollama / LM Studio / llama.cpp / Jan)
#  • In-app Whisper (faster-whisper) backend + Offline Mode guard
#
#  pynput needs a display on Linux; when it can't load, a minimal
#  stand-in is used so the trigger logic is still exercised. On
#  Windows CI the real library is used.
#
#  Run from project root:   python -m unittest tests.test_v250_features
# ============================================================

import enum
import io
import json
import os
import struct
import sys
import threading
import types
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import hotkey_listener as hk
except Exception:                       # no display / no pynput → stand-in
    class _Key(enum.Enum):
        ctrl_r = "ctrl_r"; ctrl_l = "ctrl_l"; alt_r = "alt_r"; alt_gr = "alt_gr"
        shift_r = "shift_r"; caps_lock = "caps_lock"; space = "space"
    _kb = types.SimpleNamespace(Key=_Key, Listener=object, KeyCode=object, GlobalHotKeys=object)
    _ms = types.SimpleNamespace(Listener=object, Button=object)
    sys.modules["pynput"] = types.SimpleNamespace(keyboard=_kb, mouse=_ms)
    sys.modules["pynput.keyboard"] = _kb
    sys.modules["pynput.mouse"] = _ms
    sys.modules.pop("hotkey_listener", None)
    import hotkey_listener as hk

import config
import local_asr
import local_models
import text_processor as tp
import transcriber


def _wav(rate=16000, n=16000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack(f"<{n}h", *([1000] * n)))
    return b.getvalue()


class DoubleTapTests(unittest.TestCase):

    def setUp(self):
        self.d = hk.DoubleTap()

    def _tap(self, t, key="ctrl_r", held=0.05):
        self.d.press(key, "ctrl_r", t)
        return self.d.release(key, "ctrl_r", t + held)

    def test_two_quick_taps_fire(self):
        self.assertFalse(self._tap(0.0))
        self.assertTrue(self._tap(0.25))

    def test_slow_second_tap_does_not_fire(self):
        self._tap(0.0)
        self.assertFalse(self._tap(1.0))

    def test_long_press_is_not_a_tap(self):
        self._tap(0.0)
        self.assertFalse(self._tap(0.2, held=0.6))

    def test_chord_cancels(self):
        self._tap(0.0)
        self.d.press("ctrl_r", "ctrl_r", 0.2)
        self.d.press("c", "ctrl_r", 0.22)           # Ctrl+C, not a tap
        self.assertFalse(self.d.release("ctrl_r", "ctrl_r", 0.25))
        self.assertFalse(self._tap(0.3))

    def test_typing_between_taps_cancels(self):
        self._tap(0.0)
        self.d.press("a", "ctrl_r", 0.1)
        self.assertFalse(self._tap(0.2))

    def test_key_repeat_ignored(self):
        self.d.press("ctrl_r", "ctrl_r", 0.0)
        self.d.press("ctrl_r", "ctrl_r", 0.03)      # OS auto-repeat
        self.assertFalse(self.d.release("ctrl_r", "ctrl_r", 0.06))
        self.assertTrue(self._tap(0.2))

    def test_disabled_when_no_target(self):
        self.d.press("ctrl_r", "", 0.0)
        self.assertFalse(self.d.release("ctrl_r", "", 0.05))


class PointerTriggerTests(unittest.TestCase):

    def setUp(self):
        self.events = []
        self.done = threading.Event()

        def cb(name):
            def f():
                self.events.append(name)
                self.done.set()
            return f
        self._saved = (config.MOUSE_PTT_BUTTON, config.MOUSE_PTT_MODE)
        config.MOUSE_PTT_BUTTON = ""          # never start a real global mouse hook in tests
        self.hl = hk.HotkeyListener(cb("press"), cb("release"))

    def tearDown(self):
        config.MOUSE_PTT_BUTTON, config.MOUSE_PTT_MODE = self._saved

    def _wait(self, n):
        for _ in range(50):
            if len(self.events) >= n:
                return
            self.done.wait(0.05); self.done.clear()

    def test_mouse_hold_to_talk(self):
        config.MOUSE_PTT_MODE = "hold"
        self.hl.refresh_extras()
        self.hl._mouse_event(True); self._wait(1)
        self.hl._mouse_event(False); self._wait(2)
        self.assertEqual(self.events, ["press", "release"])

    def test_mouse_toggle(self):
        config.MOUSE_PTT_MODE = "toggle"
        self.hl.refresh_extras()
        with mock.patch.object(hk, "_TOGGLE_DEBOUNCE_SEC", 0.0):
            self.hl._mouse_event(True); self._wait(1)
            self.hl._mouse_event(False)                      # release does nothing in toggle
            self.hl._mouse_event(True); self._wait(2)
        self.assertEqual(self.events, ["press", "release"])

    def test_double_tap_combo_toggles(self):
        self.hl.refresh_extras()
        with mock.patch.object(hk, "_TOGGLE_DEBOUNCE_SEC", 0.0):
            self.hl._fire_press(hk._DOUBLE_COMBO); self._wait(1)
            self.hl._fire_press(hk._DOUBLE_COMBO); self._wait(2)
        self.assertEqual(self.events, ["press", "release"])

    def test_altgr_counts_as_right_alt(self):
        self.assertEqual(hk._raw_key_name(hk.keyboard.Key.alt_gr), "alt_r")


class _FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send({"data": [{"id": "nomic-embed-text"}, {"id": "mistral-7b-instruct"},
                                 {"id": "qwen2.5-7b-instruct"}]})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self._send({"choices": [{"message": {"content": "Cleaned locally."}}]})


class LocalModelTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeLLM)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_detect_lists_chat_models_best_first(self):
        with mock.patch.object(local_models, "LOCAL_SERVERS", (("Fake", self.port),)):
            found = local_models.detect()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["base_url"], f"http://127.0.0.1:{self.port}/v1")
        self.assertEqual(found[0]["models"], ["qwen2.5-7b-instruct", "mistral-7b-instruct"])

    def test_detect_nothing_running(self):
        with mock.patch.object(local_models, "LOCAL_SERVERS", (("None", 1),)):
            self.assertEqual(local_models.detect(timeout=0.2), [])

    def test_cleanup_through_local_server(self):
        saved = {k: getattr(config, k) for k in ("LLM_BACKEND", "OLLAMA_BASE_URL", "OLLAMA_LLM_MODEL",
                                                 "CLEAN_WITH_LLM", "BRAIN_MODE", "OFFLINE_MODE")}
        try:
            config.LLM_BACKEND, config.CLEAN_WITH_LLM, config.BRAIN_MODE = "ollama", True, False
            config.OLLAMA_BASE_URL = f"http://127.0.0.1:{self.port}/v1"
            config.OLLAMA_LLM_MODEL = "qwen2.5-7b-instruct"
            config.OFFLINE_MODE = True       # local server is allowed in Offline Mode
            p = tp.TextProcessor()
            self.assertEqual(p.clean("cleaned locally"), "Cleaned locally.")
            self.assertEqual(p.last_status, "llm")
        finally:
            for k, v in saved.items():
                setattr(config, k, v)


class LocalWhisperTests(unittest.TestCase):

    def setUp(self):
        self.calls = {}
        calls = self.calls

        class FakeModel:
            def __init__(self, name, device, compute_type, download_root, **kw):
                calls["init"] = (name, device, compute_type)

            def transcribe(self, audio, **kw):
                calls["len"], calls["kw"] = len(audio), kw
                return iter([types.SimpleNamespace(text=" hello"),
                             types.SimpleNamespace(text=" world.")]), None

        self._mods = mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=FakeModel)})
        self._mods.start()
        local_asr._model, local_asr._key = None, None
        self._saved = {k: getattr(config, k) for k in ("ASR_BACKEND", "BRAIN_MODE", "OFFLINE_MODE",
                                                       "LOCAL_WHISPER_DEVICE")}
        config.ASR_BACKEND, config.BRAIN_MODE, config.LOCAL_WHISPER_DEVICE = "faster_whisper", False, "cpu"

    def tearDown(self):
        self._mods.stop()
        local_asr._model, local_asr._key = None, None
        for k, v in self._saved.items():
            setattr(config, k, v)

    def test_transcribes_in_process(self):
        out = transcriber.Transcriber().transcribe(_wav(), initial_prompt="Hey Nova",
                                                   model_override="whisper-large-v3")
        self.assertEqual(out, "hello world.")
        self.assertEqual(self.calls["init"][1:], ("cpu", "int8"))
        self.assertEqual(self.calls["kw"]["initial_prompt"], "Hey Nova")
        self.assertFalse(self.calls["kw"]["condition_on_previous_text"])

    def test_resamples_to_16k(self):
        self.assertEqual(len(local_asr._wav_to_float32(_wav(48000, 48000))), 16000)

    def test_offline_mode_blocks_cloud(self):
        config.OFFLINE_MODE = True
        for name in ("groq", "openai", "deepgram"):
            self.assertIsNone(transcriber._backend_params(name, quiet=True), name)
        self.assertIsNotNone(transcriber._backend_params("faster_whisper", quiet=True))
        with mock.patch.object(config, "GROQ_API_KEY", "x"):
            self.assertIsNone(tp._llm_params("groq"))

    def test_wake_model_override_only_for_groq(self):
        config.OFFLINE_MODE = False
        with mock.patch.object(config, "ASR_BACKEND", "openai"), \
             mock.patch.object(config, "OPENAI_API_KEY", "x"), \
             mock.patch.object(transcriber.Transcriber, "_call", return_value="ok") as call:
            transcriber.Transcriber().transcribe(_wav(), model_override="whisper-large-v3")
        self.assertEqual(call.call_args[0][1]["model"], config.OPENAI_ASR_MODEL)


class DashboardLocalTests(unittest.TestCase):

    def setUp(self):
        import web_dashboard
        self.wd = web_dashboard
        self.client = web_dashboard.app.test_client()
        self._p = [mock.patch.object(web_dashboard.auth, "is_configured", return_value=True),
                   mock.patch.object(config, "save"),
                   mock.patch.object(local_models, "warm_up_async")]
        for p in self._p:
            p.start()
        with self.client.session_transaction() as s:
            s["vf_auth"] = True
        self._saved = {k: getattr(config, k) for k in ("LLM_BACKEND", "LLM_FALLBACK_BACKEND",
                                                       "OLLAMA_BASE_URL", "OLLAMA_LLM_MODEL")}

    def tearDown(self):
        for p in self._p:
            p.stop()
        for k, v in self._saved.items():
            setattr(config, k, v)

    def test_use_local_model_as_fallback(self):
        r = self.client.post("/api/local-models/use", json={
            "base_url": "http://127.0.0.1:1234/v1", "model": "qwen2.5-7b-instruct", "role": "fallback"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(config.LLM_FALLBACK_BACKEND, "ollama")
        self.assertEqual(config.OLLAMA_LLM_MODEL, "qwen2.5-7b-instruct")

    def test_rejects_remote_url(self):
        r = self.client.post("/api/local-models/use", json={
            "base_url": "http://evil.example/v1", "model": "x", "role": "primary"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
