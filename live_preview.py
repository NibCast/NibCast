# ============================================================
#  NibCast — Live text while you speak
# ============================================================
#  While a dictation is recording, re-transcribe the last few
#  seconds every ~1–2 s and show the words in a caption above the
#  widget, so you can see it's hearing you right before you stop.
#
#  The preview is display-only: the pasted text still comes from
#  the normal full-clip transcription + cleanup when you stop.
#
#  Engines (LIVE_PREVIEW):
#    "auto"  — In-app Whisper when it's loaded (free, private, no
#              network); otherwise no preview. Default.
#    "cloud" — also allow the configured cloud ASR, on a short
#              tail window (costs quota: ~1 request every 2.5 s).
#    "off"
# ============================================================

from __future__ import annotations

import threading
import time

import config
from logger import log

WINDOW_SEC   = 6.0     # how much recent audio each preview transcribes (small = cheap on CPU)
MIN_AUDIO_SEC = 0.8    # don't preview before there's something to hear
LOCAL_EVERY  = 1.0     # seconds between previews (local engine)
CLOUD_EVERY  = 2.5     # … and with a cloud engine (quota-friendly)


def caption_text(text: str, max_chars: int = 140) -> str:
    """Last `max_chars` of the preview, cut at a word boundary, with a leading
    ellipsis when trimmed — the caption shows what you're saying *now*."""
    t = " ".join((text or "").split())
    if len(t) <= max_chars:
        return t
    tail = t[-max_chars:]
    cut = tail.find(" ")
    return "…" + (tail[cut + 1:] if 0 <= cut < 30 else tail)


def _engine():
    """(transcribe_fn, interval) for the preview, or (None, 0) when disabled."""
    mode = (getattr(config, "LIVE_PREVIEW", "auto") or "auto").lower()
    if mode == "off":
        return None, 0
    import local_asr
    if local_asr.is_loaded():
        return (lambda wav: local_asr.transcribe(wav, fast=True, quiet=True)), LOCAL_EVERY
    if mode == "cloud" and not getattr(config, "OFFLINE_MODE", False):
        import transcriber
        primary = getattr(config, "ASR_BACKEND", "groq")
        params = transcriber._backend_params(primary, quiet=True)
        if params and params.get("kind") != "faster_whisper":
            t = transcriber.Transcriber()
            return (lambda wav: t._call(wav, params)), CLOUD_EVERY
    return None, 0


class LivePreview:
    """One preview session per recording. start() → runs until stop()."""

    def __init__(self, recorder, on_text):
        self._recorder = recorder
        self._on_text = on_text
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> bool:
        fn, every = _engine()
        if fn is None:
            return False
        self._thread = threading.Thread(target=self._loop, args=(fn, every),
                                        daemon=True, name="LivePreview")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _loop(self, fn, every):
        started = time.time()
        last = ""
        while not self._stop.wait(every):
            if time.time() - started < MIN_AUDIO_SEC:
                continue
            wav = self._recorder.snapshot_wav(WINDOW_SEC)
            if not wav:
                continue
            t0 = time.time()
            try:
                text = (fn(wav) or "").strip()
            except Exception as e:
                log.debug(f"Live preview error: {e}")
                continue
            if self._stop.is_set():
                return          # recording ended while we were transcribing — don't flash stale text
            if text and text != last:
                last = text
                self._on_text(caption_text(text))
            # A slow CPU shouldn't queue previews back to back.
            spent = time.time() - t0
            if spent > every:
                self._stop.wait(min(spent, 3.0))
