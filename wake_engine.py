# ============================================================
#  NibCast — Local wake-word engine (keyword spotting)
# ============================================================
#  Listens for the wake phrase ON THIS PC with a tiny streaming
#  keyword-spotting model (sherpa-onnx, Apache-2.0), instead of
#  recording every noise and asking a speech recogniser whether
#  it was the wake phrase.
#
#    old: energy gate → record clip → Whisper (cloud) → fuzzy text match
#    new: every audio block → keyword spotter → wake (≈ instant)
#
#  Any typed phrase works — no training: the phrase is split into
#  the model's sub-word tokens. Nothing is uploaded, ambient
#  speech is never transcribed or stored, and there is no
#  per-clip network round-trip.
#
#  Optional dependencies:   pip install sherpa-onnx sentencepiece
#  Model (~17 MB, English) downloads once to ~/.nibcast/models/kws
# ============================================================

from __future__ import annotations

import importlib.util
import os
import re
import tarfile
import tempfile
import threading

import numpy as np
import requests

import config
from logger import log

MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL  = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
              f"{MODEL_NAME}.tar.bz2")
MODEL_ROOT = os.path.join(config.USER_DIR, "models", "kws")
MODEL_DIR  = os.path.join(MODEL_ROOT, MODEL_NAME)

_FILES = {
    "tokens":  "tokens.txt",
    "bpe":     "bpe.model",
    "encoder": "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "decoder": "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "joiner":  "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
}

_status = {"state": "idle", "error": "", "progress": 0}
_dl_lock = threading.Lock()


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except ValueError:
        return False


def available() -> bool:
    """Both runtime packages installed."""
    return _has("sherpa_onnx") and _has("sentencepiece")


def model_ready() -> bool:
    return all(os.path.isfile(os.path.join(MODEL_DIR, f)) for f in _FILES.values())


def status() -> dict:
    st = dict(_status)
    if st["state"] == "idle" and model_ready():
        st["state"] = "ready"
    return {**st, "available": available(), "model_ready": model_ready()}


# ── Download ──────────────────────────────────────────────────

def _safe_extract(archive: str, dest: str):
    """Extract only the model files, refusing anything that would land outside
    `dest` (absolute paths, "..", links) — a tampered archive can't write
    elsewhere on disk."""
    root = os.path.realpath(dest)
    wanted = set(_FILES.values())
    with tarfile.open(archive, "r:bz2") as tf:
        for m in tf.getmembers():
            name = os.path.basename(m.name)
            if not m.isfile() or name not in wanted:
                continue
            if m.name.startswith(("/", "\\")) or ".." in m.name.replace("\\", "/").split("/"):
                continue
            target = os.path.realpath(os.path.join(dest, MODEL_NAME, name))
            if not target.startswith(root + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(target + ".part", "wb") as out:
                while chunk := src.read(1 << 16):
                    out.write(chunk)
            os.replace(target + ".part", target)


def download() -> bool:
    """Fetch and unpack the model (once). Safe to call repeatedly."""
    if model_ready():
        _status.update(state="ready", error="", progress=100)
        return True
    if not _dl_lock.acquire(blocking=False):
        return False   # already downloading
    try:
        _status.update(state="downloading", error="", progress=0)
        os.makedirs(MODEL_ROOT, exist_ok=True)
        log.info(f"⬇️  Downloading wake-word model → {MODEL_ROOT}")
        fd, tmp = tempfile.mkstemp(suffix=".tar.bz2", dir=MODEL_ROOT)
        os.close(fd)
        try:
            with requests.get(MODEL_URL, stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0)) or 18_000_000
                got = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        _status["progress"] = min(99, int(100 * got / total))
            _safe_extract(tmp, MODEL_ROOT)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if not model_ready():
            raise RuntimeError("archive did not contain the expected model files")
        _status.update(state="ready", progress=100)
        log.info("✅ Wake-word model ready")
        return True
    except Exception as e:
        _status.update(state="error", error=str(e)[:300])
        log.error(f"❌ Wake-word model download failed: {e}")
        return False
    finally:
        _dl_lock.release()


def download_async():
    threading.Thread(target=download, daemon=True, name="KWSDownload").start()


# ── Phrase → model tokens ─────────────────────────────────────

def _clean_phrase(phrase: str) -> str:
    return " ".join(re.sub(r"[^A-Za-z' ]+", " ", phrase or "").split()).upper()


def keywords_text(phrases, threshold: float, boost: float) -> str:
    """One line per phrase in sherpa-onnx's keyword format:
       "▁ L IGHT ▁UP :2.0 #0.25 @LIGHT_UP"
    (the label after @ must not contain spaces)."""
    import sentencepiece as spm
    sp = spm.SentencePieceProcessor(model_file=os.path.join(MODEL_DIR, _FILES["bpe"]))
    lines = []
    for p in phrases:
        clean = _clean_phrase(p)
        if not clean:
            continue
        pieces = sp.encode(clean, out_type=str)
        lines.append(f"{' '.join(pieces)} :{boost:.2f} #{threshold:.2f} @{clean.replace(' ', '_')}")
    return "\n".join(lines) + ("\n" if lines else "")


# ── Spotter ───────────────────────────────────────────────────

class KeywordSpotter:
    """Streaming detector. Feed mono float32 audio with accept(); it returns the
    phrase label(s) detected in that audio (usually none)."""

    def __init__(self, phrases, threshold: float = 0.25, boost: float = 2.0,
                 num_threads: int = 1):
        import sherpa_onnx
        if not model_ready():
            raise RuntimeError("wake-word model not downloaded")
        kw = keywords_text(phrases, threshold, boost)
        if not kw.strip():
            raise ValueError("wake phrase has no usable letters")
        self._kw_path = os.path.join(MODEL_DIR, "nibcast_keywords.txt")
        with open(self._kw_path, "w", encoding="utf-8") as f:
            f.write(kw)
        p = lambda k: os.path.join(MODEL_DIR, _FILES[k])
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=p("tokens"), encoder=p("encoder"), decoder=p("decoder"),
            joiner=p("joiner"), keywords_file=self._kw_path,
            num_threads=num_threads, provider="cpu",
        )
        self._stream = self._kws.create_stream()
        self._lock = threading.Lock()
        self.phrases = list(phrases)

    def accept(self, samples: np.ndarray, sample_rate: int) -> list:
        hits = []
        with self._lock:
            self._stream.accept_waveform(sample_rate, np.ascontiguousarray(samples, dtype=np.float32))
            while self._kws.is_ready(self._stream):
                self._kws.decode_stream(self._stream)
                r = self._kws.get_result(self._stream)
                if r:
                    hits.append(r.replace("_", " ").lower())
                    self._kws.reset_stream(self._stream)
        return hits

    def reset(self):
        """Drop buffered audio (e.g. when returning to sleep after a command)."""
        with self._lock:
            self._stream = self._kws.create_stream()


def build_spotter() -> KeywordSpotter | None:
    """Spotter for the configured wake phrase, or None (with a logged reason)."""
    phrase = (getattr(config, "WAKE_WORD", "") or "").strip()
    if not phrase:
        return None
    if not available():
        log.warning("🔑 Local wake engine needs:  pip install sherpa-onnx sentencepiece")
        return None
    if not model_ready():
        log.warning("🔑 Local wake engine: model not downloaded yet (Config → Wake Word)")
        return None
    # Sensitivity 0–1 (UI) → trigger threshold: higher sensitivity = lower bar.
    sens = min(1.0, max(0.0, float(getattr(config, "WAKE_KWS_SENSITIVITY", 0.5))))
    threshold = round(0.45 - 0.35 * sens, 2)     # 0.0 → 0.45 (strict) … 1.0 → 0.10 (eager)
    try:
        spotter = KeywordSpotter([phrase], threshold=threshold)
        log.info(f"🔑 Local wake engine ready: '{phrase}' (threshold {threshold})")
        return spotter
    except Exception as e:
        log.error(f"❌ Local wake engine failed to start: {e}")
        _status.update(state="error", error=str(e)[:300])
        return None
