# ============================================================
#  NibCast — In-process local speech-to-text (faster-whisper)
# ============================================================
#  Runs Whisper inside NibCast — no server to start, no API key,
#  no audio leaving the machine. Optional dependency:
#
#      pip install faster-whisper
#
#  The model (e.g. "small.en", ~460 MB) downloads once into
#  ~/.nibcast/models on first load, then works offline. Uses an
#  NVIDIA GPU automatically when CUDA is available, else int8 CPU.
#
#  Because it's local, this backend also makes the wake-word
#  check private: Phase-1 clips are never uploaded anywhere.
# ============================================================

from __future__ import annotations

import importlib.util
import io
import os
import sys
import threading
import time
import wave

import numpy as np

import config
from logger import log, redact

MODEL_DIR = os.path.join(config.USER_DIR, "models")

# Offered in the dashboard. ".en" models are English-only but faster and more
# accurate for English; the others handle all 99 Whisper languages.
MODELS = {
    "tiny.en":        "fastest, lowest accuracy (~75 MB)",
    "base.en":        "fast on any CPU (~145 MB)",
    "small.en":       "best balance on CPU — recommended (~460 MB)",
    "medium.en":      "more accurate, slow on CPU (~1.5 GB)",
    "distil-large-v3": "near large-v3 accuracy, needs a GPU to be quick (~1.5 GB)",
    "large-v3-turbo": "best accuracy, multilingual; GPU recommended (~1.6 GB)",
    "small":          "multilingual, CPU-friendly (~460 MB)",
}
DEFAULT_MODEL = "small.en"

_lock    = threading.Lock()
_model   = None
_key     = None          # (model_name, device, compute_type) of the loaded model
_status  = {"state": "idle", "model": "", "device": "", "error": "", "load_sec": 0.0}


def available() -> bool:
    """True when the faster-whisper package is installed."""
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except ValueError:      # already imported without a spec (frozen builds)
        return "faster_whisper" in sys.modules


def status() -> dict:
    return {**_status, "available": available()}


def is_loaded() -> bool:
    return _model is not None


def _pick_device() -> tuple:
    pref = (getattr(config, "LOCAL_WHISPER_DEVICE", "auto") or "auto").lower()
    if pref == "cpu":
        return "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    if pref == "cuda":
        log.warning("🎙️  LOCAL_WHISPER_DEVICE=cuda but no CUDA device found — using CPU")
    return "cpu", "int8"


def load(model_name: str = "") -> bool:
    """Load (downloading on first use) the configured model. Safe to call repeatedly."""
    global _model, _key
    if not available():
        _status.update(state="unavailable", error="pip install faster-whisper")
        return False
    name = model_name or getattr(config, "LOCAL_WHISPER_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    device, compute = _pick_device()
    key = (name, device, compute)
    with _lock:
        if _model is not None and _key == key:
            return True
        _status.update(state="loading", model=name, device=device, error="")
        log.info(f"🎙️  Loading local Whisper '{name}' on {device} ({compute}) — "
                 f"first use downloads it to {MODEL_DIR}")
        t0 = time.time()
        try:
            from faster_whisper import WhisperModel
            os.makedirs(MODEL_DIR, exist_ok=True)
            # On a GPU, num_workers=2 lets the live preview and the final
            # transcription decode at the same time. On CPU they'd just split the
            # same cores, so there they take turns (see _infer_lock).
            _model = WhisperModel(name, device=device, compute_type=compute,
                                  download_root=MODEL_DIR,
                                  num_workers=2 if device == "cuda" else 1)
            _key = key
            _status.update(state="ready", load_sec=round(time.time() - t0, 1))
            log.info(f"🎙️  Local Whisper ready in {_status['load_sec']}s")
            return True
        except Exception as e:
            _model, _key = None, None
            _status.update(state="error", error=str(e)[:300])
            log.error(f"❌ Local Whisper failed to load '{name}': {e}")
            return False


def load_async(model_name: str = ""):
    threading.Thread(target=load, args=(model_name,), daemon=True,
                     name="LocalWhisperLoad").start()


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    """16-bit PCM WAV → mono float32 at 16 kHz (what Whisper expects)."""
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if rate != 16000 and len(audio):
        n = int(round(len(audio) * 16000 / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


# CPU: one decode at a time (a preview pass over ≤6 s greedy is short). Also
# serialises decodes against a model swap. GPU decodes run in parallel.
_infer_lock = threading.Lock()


class _NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def transcribe(wav_bytes: bytes, initial_prompt: str = "", fast: bool = False,
               quiet: bool = False) -> str:
    """`fast` = greedy decoding (live preview); `quiet` = no log line."""
    if _model is None and not load():
        return ""
    audio = _wav_to_float32(wav_bytes)
    if not len(audio):
        return ""
    translate = getattr(config, "TRANSLATE_TO_ENGLISH", False)
    lang = (getattr(config, "LANGUAGE", "") or None) if not translate else None
    t0 = time.time()
    try:
        model = _model
        with (_NoLock() if _key and _key[1] == "cuda" else _infer_lock):
            segments, _info = model.transcribe(
                audio,
                language=lang,
                task="translate" if translate else "transcribe",
                initial_prompt=initial_prompt or None,
                beam_size=1 if fast else 5,
                # Our own VAD already trimmed the clip. Not conditioning on previous
                # text stops the classic Whisper loop ("it's it's it's it's …").
                vad_filter=False,
                condition_on_previous_text=False,
            )
            # Decoding happens lazily while the segments are iterated.
            text = " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        log.error(f"❌ [Local Whisper] {e}")
        return ""
    if not quiet:
        log.info(f"✅ [Local Whisper] {redact(text)} ({time.time() - t0:.2f}s)")
    return text
