# ============================================================
#  NibCast — Audio Recorder
# ============================================================
#  Uses a single sounddevice InputStream to avoid Windows WASAPI
#  dual-stream conflicts (two streams on the same device compete
#  and one gets silence).
#
#  Pre-roll buffer (VAD fix):
#  When the VAD fires after its 0.15–0.3 s debounce period, the
#  first part of the wake phrase has already been spoken but not
#  yet buffered (recording wasn't active yet).  The pre-roll deque
#  holds the last ~20 audio blocks (~0.47 s at 44 kHz / 1024
#  blocksize).  recorder.start() prepends these blocks so Whisper
#  receives the complete "hey voice" rather than a clipped "voice".
# ============================================================

import io
import time
import wave
import threading
import collections

import numpy as np
import sounddevice as sd

import config
from logger import log

# Fixed audio block size (samples per callback). With no explicit blocksize,
# PortAudio/WASAPI picks its own — often tiny and variable — which on slower or
# busier machines starves the Python callback and produces a storm of
# "input overflow" events. Each overflow drops audio, so the wake clip arrives
# choppy and Whisper mishears it (or the clip is too short and gets skipped).
# Pinning the block size makes delivery deterministic: it fixes the overflow AND
# makes the pre-roll buffer below cover a known, constant span of time.
_BLOCKSIZE = 1024

# Pre-roll: number of blocks to keep in the look-back buffer.
# 20 blocks × 1024 samples / 44100 Hz ≈ 0.46 s — enough to cover
# the longest VAD trigger debounce (0.3 s) with comfortable margin.
_PREROLL_BLOCKS = 20


class AudioRecorder:
    def __init__(self):
        self._frames          = []
        self._recording       = False
        self._lock            = threading.Lock()
        self._stream          = None
        self._monitor_hook    = None
        self._level_hooks     = []   # secondary hooks for mic-level reporting
        self._actual_rate     = config.SAMPLE_RATE
        self._preroll         = collections.deque(maxlen=_PREROLL_BLOCKS)
        # Throttle overflow logging — see _callback().
        self._overflow_count    = 0
        self._last_overflow_log = 0.0

    # ── Monitor hook (for VAD shared stream) ──────────────────

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def stream_open(self) -> bool:
        """True if the shared input stream is currently bound to a device.
        False after _open_stream() failed (bad INPUT_DEVICE index, mic
        permission denied, device already in use, etc.) — checked once at
        startup so that failure can surface to the UI instead of leaving
        the VAD permanently silent with no audio ever arriving."""
        return self._stream is not None

    def set_monitor_hook(self, hook):
        """Replace the primary VAD hook (VoiceActivator)."""
        self._monitor_hook = hook

    def add_level_hook(self, hook):
        """Add a secondary hook called with every chunk's RMS (float 0-1).
        Used for real-time mic level reporting without conflicting with VAD."""
        self._level_hooks.append(hook)

    def open_persistent_stream(self):
        if self._stream is not None:
            return
        log.info("🎤 Persistent stream opened (VAD active)")
        self._open_stream(log_open=False)

    def close_persistent_stream(self):
        self._monitor_hook = None
        if not self._recording:
            self._close_stream()

    # ── Per-session recording API ──────────────────────────────

    def start(self):
        """Begin buffering frames.  Prepends pre-roll so wake phrases aren't clipped."""
        with self._lock:
            # Seed the new session with the audio that arrived BEFORE the VAD fired.
            # Without this, the first 0.15–0.3 s of "hey voice" would be missing.
            self._frames    = list(self._preroll)
            self._recording = True
        if self._stream is None:
            device = config.INPUT_DEVICE
            log.info(f"🎤 Recording started (device={device if device is not None else 'default'})")
            self._open_stream(log_open=False)
        else:
            log.info(f"🎤 Recording started (shared stream, {len(self._preroll)}-block pre-roll)")

    def stop(self) -> bytes:
        with self._lock:
            self._recording = False

        if self._monitor_hook is None:
            self._close_stream()

        log.info(f"🛑 Recording stopped — {len(self._frames)} frames")

        if not self._frames:
            return b""
        return self._frames_to_wav()

    # ── Internal ──────────────────────────────────────────────

    def _open_stream(self, log_open=True):
        device = config.INPUT_DEVICE
        target_rate = config.SAMPLE_RATE
        try:
            info = sd.query_devices(device, 'input')
            native = int(info['default_samplerate'])
            dev_name = info.get('name', str(device or 'default'))
            if log_open:
                log.info(f"🎤 Opening mic stream — device: {dev_name!r} "
                         f"(native {native} Hz, configured {config.SAMPLE_RATE} Hz)")
            if native != target_rate:
                log.info(f"  → Using native rate {native} Hz (WASAPI compatibility)")
                target_rate = native
        except Exception:
            if log_open:
                log.info(f"🎤 Opening mic stream (device={device if device is not None else 'default'})")

        self._actual_rate = target_rate
        try:
            # blocksize: pin the callback chunk so delivery is steady (see
            # _BLOCKSIZE). latency="high": ask PortAudio for a larger internal
            # buffer — this trades a few ms of extra delay (irrelevant for
            # dictation) for far more headroom against input overflow on slower
            # machines, which is the difference between the wake word working or
            # not on a given laptop.
            self._stream = sd.InputStream(
                samplerate=target_rate,
                channels=config.CHANNELS,
                dtype="int16",
                device=device,
                blocksize=_BLOCKSIZE,
                latency="high",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            log.error(f"❌ Failed to open input device: {e}")
            self._recording = False
            self._stream    = None

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            # This runs on sounddevice's realtime audio thread. log.warning()
            # does disk I/O under the GIL — calling it on every dropped block
            # (input overflow) starves the callback further and snowballs the
            # very overflow it's reporting, which shows up as wake-word lag on
            # slower machines. Count silently and emit at most one summary line
            # every 5 s instead.
            self._overflow_count += 1
            now = time.monotonic()
            if now - self._last_overflow_log >= 5.0:
                log.warning(f"Audio stream status: {status} "
                            f"({self._overflow_count} event(s) since last report)")
                self._last_overflow_log = now
                self._overflow_count = 0
        data    = indata.copy()
        float32 = data.astype(np.float32) / 32768.0
        if self._monitor_hook:
            try:
                self._monitor_hook(float32)
            except Exception:
                pass
        if self._level_hooks:
            rms = float(np.sqrt(np.mean(float32 ** 2)))
            for hook in self._level_hooks:
                try:
                    hook(rms)
                except Exception:
                    pass
        with self._lock:
            if self._recording:
                self._frames.append(data)
            else:
                self._preroll.append(data)

    def _frames_to_wav(self) -> bytes:
        audio_int16 = np.concatenate(self._frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(config.CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(self._actual_rate)
            wf.writeframes(audio_int16.tobytes())
        buf.seek(0)
        log.info(f"📦 WAV created — {buf.getbuffer().nbytes / 1024:.1f} KB "
                 f"@ {self._actual_rate} Hz")
        return buf.read()


# ── Module helpers (for the settings UI) ────────────────────

def list_input_devices():
    try:
        devs = sd.query_devices()
    except Exception:
        return []
    out = []
    for i, d in enumerate(devs):
        if (d.get("max_input_channels") or 0) > 0:
            label = f"{i}: {d.get('name','?')} ({d.get('hostapi','?')})"
            out.append((i, label))
    return out
