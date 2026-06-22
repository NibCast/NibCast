# ============================================================
#  NibCast — Voice Activity Detection (VAD) Activator
# ============================================================
#  Modes: "sleep" (Phase 1, wake-word hunting) and
#         "command" (Phase 2, dictation after wake confirmed).
#  Switch with set_mode().  Post-hotkey cooldown via set_cooldown().
# ============================================================

import threading
import time

import numpy as np

import config
from logger import log

_HOTKEY_COOLDOWN_SEC = 2.0


class VoiceActivator:
    # Seconds of sustained energy required before firing. Matches the default
    # WAKE_WORD_TRIGGER_SEC (Phase 1) — Phase 2 (command mode) has no config
    # override, so it must be at least as responsive or command-mode onset can
    # be missed on mics/rooms where Phase 1 still triggers reliably.
    _TRIGGER_SEC   = 0.15
    # Asymmetric EMA: fast attack so voice peaks cross the threshold quickly,
    # slow release so brief transients (keyboard, cough) don't sustain above it.
    _ATTACK_ALPHA  = 0.7
    _RELEASE_ALPHA = 0.15
    # Hard ceiling for the sleep-mode wake gate, regardless of config/auto-raise.
    # Above this a normal speaking voice can't reliably cross, so the wake word
    # would never fire. Keep in sync with the auto-raise cap in main.py.
    _WAKE_THRESHOLD_CEIL = 0.08
    # Command-mode (Phase 2) gate adapts to the mic. A fixed 0.030 silenced quiet
    # mics mid-sentence: when a user's speaking voice sits below the gate, the tiny
    # gaps between words read as "done speaking". The effective gate is the noise
    # floor × _CMD_NOISE_MULT, never below _CMD_THRESHOLD_FLOOR (so true silence
    # still ends the turn) and never above the configured VOICE_VAD_THRESHOLD.
    _CMD_THRESHOLD_FLOOR = 0.008
    _CMD_NOISE_MULT      = 2.0

    def __init__(self, recorder, on_start_cb, on_stop_cb):
        self._recorder     = recorder
        self._on_start_cb  = on_start_cb
        self._on_stop_cb   = on_stop_cb

        self._running        = False
        self._is_recording   = False
        self._mode           = "sleep"
        self._above_since    = None
        self._below_since    = None
        self._cooldown_until = 0.0
        self._record_start   = 0.0
        self._rms_ema        = 0.0
        self._noise_floor    = 0.0   # ambient level, tracked while idle in sleep mode
        self._phase1_busy    = False

    # ── Public API ─────────────────────────────────────────────

    def start(self):
        self._running = True
        thr = getattr(config, "WAKE_WORD_VAD_THRESHOLD", config.VOICE_VAD_THRESHOLD)
        sil = getattr(config, "WAKE_WORD_SILENCE_SEC",   config.VOICE_VAD_SILENCE_SEC)
        log.info(f"VAD: monitoring (threshold={thr}, silence={sil}s, mode=sleep)")
        self._recorder.set_monitor_hook(self._on_energy)
        self._recorder.open_persistent_stream()

    def stop(self):
        self._running = False
        self._recorder.close_persistent_stream()

    def set_mode(self, mode: str):
        """
        'sleep'   → Phase 1: higher threshold, shorter silence, max-duration cap.
        'command' → Phase 2: normal threshold/silence, no duration cap.
        """
        if mode not in ("sleep", "command"):
            return
        prev = self._mode
        self._mode = mode
        if mode != prev:
            if mode == "command":
                # Reset EMA so Phase-1 residual energy doesn't suppress Phase-2 onset
                self._rms_ema    = 0.0
                self._above_since = None
            log.info(f"VAD: mode → {mode}")

    def set_cooldown(self, seconds: float = _HOTKEY_COOLDOWN_SEC):
        self._cooldown_until = time.time() + seconds
        self._above_since    = None
        log.info(f"VAD: cooldown armed ({seconds:.1f}s) — suppressing auto-trigger")

    def set_phase1_busy(self, busy: bool):
        """
        Signal that a Phase-1 clip is being processed (True) or has completed (False).
        While busy, new Phase-1 recordings are blocked so Groq calls don't pile up.
        """
        self._phase1_busy = busy

    # ── Audio callback ─────────────────────────────────────────

    def _on_energy(self, data: np.ndarray):
        if not self._running:
            return

        samples = data[:, 0] if data.ndim > 1 else data
        raw_rms = float(np.sqrt(np.mean(samples ** 2)))

        # Asymmetric EMA: attack is fast so genuine voice peaks reach the threshold
        # quickly; release is slow so brief transients don't sustain above it.
        alpha = self._ATTACK_ALPHA if raw_rms > self._rms_ema else self._RELEASE_ALPHA
        self._rms_ema = alpha * raw_rms + (1 - alpha) * self._rms_ema
        rms = self._rms_ema
        now = time.time()

        # Track the ambient noise floor while idle in sleep mode (never during a
        # recording, so speech doesn't pollute it). Snap down to the quietest
        # recent level, creep up slowly — this gives a stable estimate of room
        # tone that command mode uses to set its silence gate per-mic.
        if (self._mode == "sleep" and not self._is_recording
                and not self._recorder.is_recording):
            if self._noise_floor <= 0.0 or raw_rms < self._noise_floor:
                self._noise_floor = raw_rms
            else:
                self._noise_floor += 0.002 * (raw_rms - self._noise_floor)

        if self._mode == "sleep":
            # Clamp the wake gate to a safe band. The upper bound matters: a stale
            # config.json or an over-eager auto-raise (see main.py Wake L1b) can push
            # this above a normal speaking-voice level, which silently makes the wake
            # word impossible to trigger — exactly the "it never caught anything"
            # failure on low-gain mics. _WAKE_THRESHOLD_CEIL still rejects room tone
            # but stays below spoken-phrase energy, so even a bad config self-heals.
            _cfg_thr    = getattr(config, "WAKE_WORD_VAD_THRESHOLD", config.VOICE_VAD_THRESHOLD)
            threshold   = min(self._WAKE_THRESHOLD_CEIL, max(0.01, _cfg_thr))
            silence_sec = max(0.3,  getattr(config, "WAKE_WORD_SILENCE_SEC",   config.VOICE_VAD_SILENCE_SEC))
            max_rec_sec = max(1.0,  getattr(config, "WAKE_WORD_MAX_RECORD_SEC", 3.5))
        else:
            # Adaptive gate (see _CMD_NOISE_MULT): lower it toward the measured
            # noise floor on quiet mics so dictation isn't cut off between words,
            # but never above the configured value and never below the floor.
            _cfg_thr    = max(0.005, config.VOICE_VAD_THRESHOLD)
            _adaptive   = max(self._CMD_THRESHOLD_FLOOR, self._noise_floor * self._CMD_NOISE_MULT)
            threshold   = min(_cfg_thr, _adaptive)
            silence_sec = max(0.3,   config.VOICE_VAD_SILENCE_SEC)
            max_rec_sec = None

        hotkey_active = self._recorder.is_recording
        in_cooldown   = now < self._cooldown_until

        # ── Max-duration guard (Phase 1 only) ────────────────
        # If a Phase-1 clip runs longer than max_rec_sec, force-stop it.
        # This prevents a long ambient sentence from being forwarded to
        # the cloud ASR — "hey voice" should never take more than 3 s.
        if self._is_recording and max_rec_sec is not None:
            if now - self._record_start > max_rec_sec:
                self._is_recording = False
                self._below_since  = None
                log.info(f"VAD: Phase 1 clip too long (>{max_rec_sec:.1f}s) — force-stopping")
                threading.Thread(target=self._fire_stop, daemon=True,
                                 name="VADMaxDur").start()
                return

        trigger_sec = (getattr(config, "WAKE_WORD_TRIGGER_SEC", self._TRIGGER_SEC)
                       if self._mode == "sleep" else self._TRIGGER_SEC)

        if rms > threshold:
            self._below_since = None
            # Also block new Phase-1 triggers while the previous clip is in-flight
            phase1_blocked = (self._mode == "sleep" and self._phase1_busy)
            if hotkey_active or in_cooldown or phase1_blocked:
                self._above_since = None
            elif not self._is_recording:
                if self._above_since is None:
                    self._above_since = now
                elif now - self._above_since >= trigger_sec:
                    self._above_since  = None
                    self._is_recording = True
                    self._record_start = now
                    log.info("VAD: voice detected → starting recording")
                    threading.Thread(target=self._fire_start, daemon=True,
                                     name="VADStart").start()
        else:
            self._above_since = None
            if self._is_recording:
                if self._below_since is None:
                    self._below_since = now
                elif now - self._below_since >= silence_sec:
                    self._below_since  = None
                    self._is_recording = False
                    log.info("VAD: silence detected → stopping recording")
                    threading.Thread(target=self._fire_stop, daemon=True,
                                     name="VADStop").start()
            else:
                self._below_since = None

    def _fire_start(self):
        try:
            self._on_start_cb()
        except Exception as e:
            log.error(f"VAD on_start_cb error: {e}")

    def _fire_stop(self):
        try:
            self._on_stop_cb()
        except Exception as e:
            log.error(f"VAD on_stop_cb error: {e}")
