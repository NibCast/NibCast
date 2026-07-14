# ============================================================
#  NibCast — Main Entry Point
# ============================================================
__version__ = "2.4.1"

import sys

# ── Preflight checks (run BEFORE any heavy import) ───────────────
# A fresh download started with the wrong interpreter — old Python, or deps
# never installed because setup.bat wasn't run — otherwise dies with a raw
# traceback that means nothing to a non-technical user. Fail with a clear,
# actionable message instead. This is the first code that runs.
if sys.version_info < (3, 10):
    sys.exit(
        "NibCast requires Python 3.10 or newer "
        f"(you have {sys.version_info.major}.{sys.version_info.minor}).\n"
        "Install a newer Python from https://www.python.org/downloads/ "
        "(check 'Add Python to PATH'), then re-run setup.bat."
    )

import io
import re
import wave
import math
import struct
import time
import threading
import webbrowser
import subprocess
import os
import platform
import argparse

try:
    import config
    import database as db
    import target_manager as tm
    import web_dashboard
    import state
    import notifier

    from audio_recorder   import AudioRecorder
    from transcriber      import Transcriber, _is_hallucination, has_configured_backend
    from text_processor   import TextProcessor
    from text_injector    import TextInjector
    from hotkey_listener  import HotkeyListener
    from tray_ui          import TrayUI
    from settings_window  import SettingsWindow
    from floating_widget  import FloatingWidget
    from editor_popup     import edit_text_blocking
    from logger           import log
except ImportError as _e:
    _missing = getattr(_e, "name", "") or "a required package"
    _tk_hint = ("\nNote: 'tkinter' ships with the standard python.org installer "
                "but is missing from some slim builds.") if _missing == "tkinter" else ""
    sys.exit(
        f"NibCast could not start — a required module is missing: {_missing}.\n\n"
        "This almost always means the dependencies aren't installed, or NibCast "
        "was launched with the wrong Python.\n\n"
        "Fix (first time): double-click  setup.bat  in the NibCast folder, then "
        "launch from the desktop icon.\n"
        "Or manually, from the NibCast folder:\n"
        "    python -m venv venv\n"
        "    venv\\Scripts\\pip install -r requirements.txt\n"
        "    venv\\Scripts\\pythonw main.py"
        f"{_tk_hint}"
    )


# ── Open the dashboard as a desktop-style app window ────────────
# Launches Chrome/Edge in chromeless --app= mode so the dashboard looks like
# a desktop app rather than a browser tab. Shared by open_dashboard_window()
# (tray / widget double-click) and the single-instance guard below (so
# double-clicking the desktop icon while already running still "opens the
# app" instead of dropping into a regular browser tab).
def _open_as_app_window(url: str) -> bool:
    try:
        if platform.system() == "Windows":
            chrome_paths = [
                os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                             "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                             "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA",
                                            os.path.expanduser("~\\AppData\\Local")),
                             "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                             "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                             "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
            for path in chrome_paths:
                if os.path.exists(path):
                    subprocess.Popen([path, f"--app={url}"])
                    return True
        return bool(webbrowser.open(url))
    except Exception as e:
        log.error(f"Failed to open dashboard window: {e}")
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False


# ── Windows process identity ──────────────────────────────────
# When running as NibCast.exe (PyInstaller build), Task Manager already shows
# "NibCast" because the executable is named NibCast.exe.
# When running from source via pythonw.exe, Task Manager shows "Python" —
# this is a Windows limitation; the process name equals the .exe name.
# SetCurrentProcessExplicitAppUserModelID fixes taskbar icon grouping in both cases.
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dubba.NibCast.2.3")
    except Exception:
        pass


# ── Single-instance guard ───────────────────────────────────────
# Without this, autostart (install.py registers a Run-key entry) plus a manual
# launch — or double-clicking the shortcut twice — spawn a second process.
# The second instance's dashboard thread fails silently (port 7171 already
# bound), so it runs with no visible UI while its VoiceActivator competes with
# the first for the microphone and global hotkeys, and both independently
# read/write ~/.nibcast/config.json (e.g. the wake-word VAD threshold
# auto-raise), causing flaky wake-word detection.
if platform.system() == "Windows":
    import ctypes
    _ERROR_ALREADY_EXISTS = 183
    _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "NibCast_SingleInstance_Mutex")
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        # The desktop shortcut / Start Menu entry is the only "open NibCast" action
        # most users know — if they double-click it while already running (e.g.
        # autostart launched it, or they forgot it's in the tray), give them the
        # dashboard they were after instead of just telling them to go find the
        # tray icon. Only fall back to the dialog if no browser could be opened.
        opened = _open_as_app_window("http://localhost:7171")
        if not opened:
            ctypes.windll.user32.MessageBoxW(
                None,
                "NibCast is already running.\n\n"
                "Look for its icon in the system tray (click the ^ arrow to show "
                "hidden icons) — only one copy can run at a time.",
                "NibCast", 0x40,  # MB_ICONINFORMATION
            )
        sys.exit(0)

db.init_db()

recorder  = AudioRecorder()
transcrib = Transcriber()
processor = TextProcessor()
injector  = TextInjector()
settings  = SettingsWindow()
widget    = FloatingWidget()
tray      = None
voice_act = None

web_dashboard.set_inject_callback(injector.inject)
recorder.add_level_hook(web_dashboard.update_mic_level)

_record_start     = 0.0
_error_seq        = 0
_shutdown_evt     = threading.Event()
_recording_source = "hotkey"   # "hotkey" | "vad"

# Set the first time an empty transcript is traced back to "no ASR backend
# configured" — shows one prominent, actionable error instead of staying
# silent (VAD) or showing the misleading "no speech detected" (hotkey) on
# every single attempt.
_asr_unconfigured_warned = False

# Tracks consecutive Phase-1 clips that hit the max-duration cap (ambient media/TV).
# After 3 in a row, a warning is logged with the exact threshold to set.
_ambient_clip_streak = 0

# Tracks consecutive clips that are too quiet for ASR but NOT dead silence —
# i.e. the user is speaking but the Windows mic input level is set too low, so
# every dictation gets silently dropped. After a few in a row we surface a
# clear, actionable "raise your mic level" hint instead of the generic notice.
_near_silence_streak = 0

# ── Wake-word two-phase state ────────────────────────────────
# All three globals are guarded by _wake_lock — they are read and written from
# the VAD thread, the hotkey-release thread, and the _window_timeout timer thread.
_wake_lock                = threading.Lock()
_vad_awake_until          = 0.0
_vad_is_command_recording = False
_phase1_busy_since        = 0.0

# Undo last paste — stores the text and char length of the most recent injection
# so the user can trigger an undo hotkey to remove it.
_last_paste_text = ""
_last_paste_chars = 0

# Grace period added to _is_vad_awake() to cover VAD trigger delay (0.3 s)
# and thread scheduling — prevents Phase 2 from falling back to Phase 1
# when the user speaks just before the nominal window closes.
_VAD_AWAKE_GRACE = 1.2

def _is_vad_awake() -> bool:
    with _wake_lock:
        return time.time() < _vad_awake_until + _VAD_AWAKE_GRACE

def _arm_vad_awake():
    """Wake word confirmed — open a command window and switch VAD to command mode."""
    global _vad_awake_until
    secs = float(getattr(config, "WAKE_WORD_LISTEN_SEC", 12.0))
    with _wake_lock:
        _vad_awake_until = time.time() + secs
    log.info(f"👂 Listening for command ({secs:.0f}s window)…")
    # Switch to command mode: normal threshold + silence window + no duration cap
    if voice_act is not None:
        voice_act.set_mode("command")
    # Ding + teal widget glow tells the user the wake was confirmed and they can speak
    notifier.ding_start()
    widget.show_awake()

    # Safety timer: if no Phase-2 recording starts before the window closes,
    # reset VAD back to sleep so the wake word can be triggered again.
    # Without this, mode stays "command" indefinitely after a timed-out window.
    def _window_timeout():
        if _vad_is_command_recording:
            return  # Phase-2 is in progress — finish_command_recording handles the reset
        if voice_act is not None:
            current_mode = getattr(voice_act, "_mode", "sleep")
            if current_mode == "command":
                log.info("👂 Command window timed out without command — returning to sleep")
                voice_act.set_mode("sleep")
                state.set_state("idle")
                widget.hide()

    t = threading.Timer(secs + 0.5, _window_timeout)
    t.daemon = True
    t.start()

def _clear_phase1_busy():
    global _phase1_busy_since
    with _wake_lock:
        _phase1_busy_since = 0.0
    if voice_act is not None:
        voice_act.set_phase1_busy(False)

def _disarm_vad_awake():
    """Close the command window.  Mode returns to sleep AFTER the command recording finishes,
    not here — caller must set mode=sleep when the recording pipeline completes."""
    global _vad_awake_until
    with _wake_lock:
        _vad_awake_until = 0.0

def _finish_command_recording():
    """Called at the end of every Phase-2 recording (success, cancel, or error).
    Resets the command flag and returns VAD to sleep mode."""
    global _vad_is_command_recording
    with _wake_lock:
        _vad_is_command_recording = False
    if voice_act is not None:
        voice_act.set_mode("sleep")

# Command Mode state
_selected_text = ""
_command_start = 0.0

# Voice command patterns — detected at the end of clean transcripts
_VOICE_CMDS = [
    (re.compile(r'[,.]?\s*(press enter|hit enter|submit|send it)\s*[.!]?$',    re.IGNORECASE), "enter"),
    (re.compile(r'[,.]?\s*(new line|new paragraph|line break)\s*[.!]?$',       re.IGNORECASE), "enter"),
    (re.compile(r'[,.]?\s*(press tab|next field)\s*[.!]?$',                    re.IGNORECASE), "tab"),
    (re.compile(r'[,.]?\s*(press escape|press esc|cancel it)\s*[.!]?$',        re.IGNORECASE), "escape"),
]


# ── Wake-word fuzzy matching ──────────────────────────────────

def _jaro(s1: str, s2: str) -> float:
    """Jaro string similarity [0–1]. No external libraries required."""
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    if l1 == 0 or l2 == 0:
        return 0.0
    win = max(max(l1, l2) // 2 - 1, 0)
    m1 = [False] * l1
    m2 = [False] * l2
    matches = 0
    for i in range(l1):
        for j in range(max(0, i - win), min(i + win + 1, l2)):
            if not m2[j] and s1[i] == s2[j]:
                m1[i] = m2[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(l1):
        if not m1[i]:
            continue
        while not m2[k]:
            k += 1
        if s1[i] != s2[k]:
            t += 1
        k += 1
    return (matches / l1 + matches / l2 + (matches - t / 2) / matches) / 3


def _match_wake_word(raw_text: str, wake_word: str) -> tuple:
    """
    Return (matched: bool, remaining_text: str).

    Match hierarchy (fastest / strictest first):

    1. Full exact match — wake phrase found verbatim within first 3 transcript words.
    2. Shorter transcript suffix — Whisper dropped leading word(s); transcript is a
       suffix of the wake phrase (exact, same word count required).
    3. Same-length fuzzy — transcript has the right number of words but Whisper
       substituted one or both:
         3a. Last word exact  → handles "a voice" when wake = "hey voice"
         3b. First word exact + last word Jaro ≥ 0.45 → handles "hey boys" ≈ "hey voice"
         3c. All words Jaro ≥ 0.50 (average) → catches remaining phonetic substitutions
    """
    def _words(s: str) -> list:
        return re.sub(r"[^\w\s]", "", s.lower()).split()

    t_words = _words(raw_text)
    w_words = _words(wake_word)
    n       = len(w_words)

    if not w_words or not t_words:
        return False, ""

    def _remaining(skip: int) -> str:
        orig = raw_text.split()
        return " ".join(orig[skip:]).strip().lstrip(",.!?;:")

    # ── 1. Full exact match ───────────────────────────────────
    search_limit = min(3, max(1, len(t_words) - n + 1))
    for i in range(search_limit):
        if t_words[i : i + n] == w_words:
            return True, _remaining(i + n)

    # ── 2. Shorter transcript (Whisper dropped leading word) ──
    # Require at least 2 matched words so a single ambient word can't confirm
    # the wake phrase (e.g. Whisper returns "voice" alone → no match).
    if n >= 3:
        min_sfx = max(2, (n + 1) // 2)
        for sfx_len in range(n - 1, min_sfx - 1, -1):
            sfx = w_words[-sfx_len:]
            if len(t_words) == sfx_len and t_words == sfx:
                log.info(f"🔑 Wake partial match ({sfx_len}/{n} words: {sfx})")
                return True, _remaining(sfx_len)

    # ── 3. Same-length fuzzy (Whisper phonetic substitution) ──
    # Raised thresholds vs. original to reduce ambient false-positive confirmations.
    if n >= 2 and len(t_words) == n:
        # 3a: last word exact — only accept when transcript has ≥2 words
        # (single-word exact matches are caught by rule 1 or allowed only when n==1)
        if n >= 2 and t_words[-1] == w_words[-1] and t_words[0] == w_words[0]:
            # Both first AND last must match exactly for 2-word phrases
            log.info(f"🔑 Wake fuzzy 3a: first+last exact ({t_words[0]!r},{t_words[-1]!r})")
            return True, _remaining(n)

        # 3b: first word exact + last word phonetically similar.
        # The first word of a 2-word wake phrase is usually a ubiquitous interjection
        # ("hey", "ok", "a"), so an exact first-word match carries almost no signal on
        # its own — background TV/video constantly produces "hey ___". The second-word
        # bar therefore must be high or media mishears slip through (e.g. "Hey Fowler"
        # ≈ "hey flow" scores Jaro 0.76 and used to confirm a false wake). Floor is
        # configurable via WAKE_WORD_FUZZY_THRESHOLD (default 0.80).
        _fuzzy_floor = getattr(config, "WAKE_WORD_FUZZY_THRESHOLD", 0.80)
        if t_words[0] == w_words[0] and n == 2:
            sim = _jaro(t_words[1], w_words[1])
            if sim >= _fuzzy_floor:
                log.info(f"🔑 Wake fuzzy 3b: first exact + last Jaro={sim:.2f} "
                         f"({t_words[1]!r}≈{w_words[1]!r})")
                return True, _remaining(n)

        # 3c: all words phonetically similar (raised floors: each ≥0.55, avg ≥0.65)
        sims = [_jaro(t_words[i], w_words[i]) for i in range(n)]
        if n == 2:
            # For a 2-word phrase the first word is a ubiquitous interjection
            # ("hey", "ok") that matches almost anything, so a perfect 1.0 there
            # would drag the average up and let a weak content word through —
            # this is exactly how "hey fowler"/"hey floor" (content Jaro ≈0.76)
            # used to confirm as "hey flow". The discriminative *content* word
            # (last) must therefore clear the same high floor as rule 3b; the
            # first word only needs to be plausible. Rule 3b already covers the
            # case where the first word matches exactly.
            ok = sims[-1] >= _fuzzy_floor and sims[0] >= 0.55
        else:
            ok = all(s >= 0.55 for s in sims) and sum(sims) / n >= 0.65
        if ok:
            log.info(f"🔑 Wake fuzzy 3c: avg Jaro={sum(sims)/n:.2f} {list(zip(t_words, w_words))}")
            return True, _remaining(n)

    return False, ""


# ── RMS silence check ────────────────────────────────────────

def _wav_rms(wav_bytes: bytes) -> float:
    """Compute normalised RMS energy of WAV PCM data (0.0 – 1.0)."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wf:
            frames = wf.readframes(wf.getnframes())
        n = len(frames) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", frames)
        rms = math.sqrt(sum(s * s for s in samples) / n) / 32768.0
        return rms
    except Exception:
        return 1.0  # assume non-silent on parse error


def _wav_peak_rms(wav_bytes: bytes, window_sec: float = 0.1) -> float:
    """Max short-window RMS of WAV PCM data (0.0 – 1.0).

    The VAD gate acts on a fast-attack EMA, so what crosses the threshold is
    the clip's loudest moments — not its average. A media clip can average
    0.09 RMS yet spike past a 0.19 gate; auto-raise decisions must therefore
    look at the peak window, or they never fire on exactly the clips that
    keep triggering.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        n = len(frames) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", frames)
        win = max(1, int(sr * window_sec))
        peak_sq = 0.0
        for i in range(0, n, win):
            chunk = samples[i:i + win]
            mean_sq = sum(s * s for s in chunk) / len(chunk)
            if mean_sq > peak_sq:
                peak_sq = mean_sq
        return math.sqrt(peak_sq) / 32768.0
    except Exception:
        return 1.0  # assume loud on parse error


_RMS_SILENCE_THRESHOLD = 0.003  # below this → almost certainly silence


# ── Clean quit ───────────────────────────────────────────────

def _do_quit():
    log.info("NibCast shutting down…")
    _shutdown_evt.set()
    sys.exit(0)


# ── Pipeline ─────────────────────────────────────────────────

def on_hotkey_press():
    global _record_start, _recording_source
    _recording_source = "hotkey"
    target_info = tm.detect_target()
    log.info(f"🎯 Target: {target_info['label']}")
    state.set_state("recording")
    if tray: tray.set_recording()
    widget.show_recording()
    notifier.ding_start()
    _record_start = time.time()
    recorder.start()


def on_vad_press():
    global _record_start, _recording_source, _vad_is_command_recording

    # Pause VAD when a competing voice app is the active window
    pause_apps = [a.lower() for a in getattr(config, "VAD_PAUSE_APPS", [])]
    if pause_apps:
        try:
            active = tm._active_window_name().lower()
            if any(a in active for a in pause_apps):
                log.info(f"VAD skipped — voice app active: {active!r}")
                if voice_act is not None:
                    voice_act.set_cooldown(2.0)
                return
        except Exception:
            pass

    _recording_source = "vad"
    target_info = tm.detect_target()
    log.info(f"🎯 VAD Target: {target_info['label']}")
    _record_start = time.time()
    recorder.start()
    awake = _is_vad_awake()
    with _wake_lock:
        # When the command window was never armed, _vad_awake_until is 0.0 and
        # the raw subtraction prints a nonsensical negative epoch time
        # (e.g. -1781470604s). Report 0 in that case for a readable log.
        until_delta = round(_vad_awake_until - time.time(), 2) if _vad_awake_until > 0 else 0.0
    log.info(f"VAD press — awake={awake}  window_remaining={until_delta}s")

    if awake:
        # Phase 2: consume the window so a second VAD trigger doesn't steal the slot.
        _disarm_vad_awake()
        with _wake_lock:
            _vad_is_command_recording = True
        log.info("Phase 2 command recording started")
        state.set_state("recording")
        if tray: tray.set_recording()
        widget.show_recording()
    else:
        # Phase 1: ambient check for wake phrase — no widget animation until wake confirmed
        with _wake_lock:
            _vad_is_command_recording = False


def _flash_error(short_label: str, full_msg: str):
    global _error_seq
    _error_seq += 1
    my_seq = _error_seq

    state.set_state("error", error=full_msg)
    widget.show_error(short_label)
    notifier.ding_error()
    if tray: tray.set_idle()

    def _reset():
        time.sleep(3.0)
        if my_seq == _error_seq and state.snapshot()["state"] == "error":
            state.set_state("idle")
    threading.Thread(target=_reset, daemon=True).start()


def on_cancel_recording():
    """Cancel the current recording without sending it to ASR or pasting anything.
    Triggered by the widget stop button (■) or by clicking the awake (READY) state."""
    if recorder.is_recording:
        recorder.stop()
    _disarm_vad_awake()
    _finish_command_recording()   # always safe to call; resets flag + returns to sleep
    state.set_state("idle")
    if tray: tray.set_idle()
    widget.hide()
    if voice_act is not None:
        voice_act.set_cooldown(1.0)
    log.info("🚫 Recording cancelled by user (widget stop button)")


def _arm_vad_cooldown():
    """Arm VAD cooldown after any hotkey session ends (success, silence, or error)."""
    if voice_act is not None and _recording_source == "hotkey":
        voice_act.set_cooldown()


def _expand_snippets(text: str) -> str:
    """If the entire utterance matches a configured snippet phrase, return its expansion."""
    snippets = getattr(config, "SNIPPETS", {})
    if not snippets:
        return text
    normalized = re.sub(r'[.!?]+$', '', text.strip()).lower().strip()
    for phrase, expansion in snippets.items():
        if normalized == phrase.lower().strip():
            log.info(f"📎 Snippet: '{phrase}' → '{expansion}'")
            return expansion
    return text


def _apply_voice_commands(text: str) -> tuple:
    """
    Strip a trailing voice command from text and return (clean_text, keys_to_press).
    E.g. "Hello world, press enter." → ("Hello world", ["enter"])
    """
    for pattern, key in _VOICE_CMDS:
        m = pattern.search(text)
        if m:
            stripped = text[:m.start()].rstrip(' ,.')
            log.info(f"🔤 Voice command detected: '{key}'")
            return stripped, [key]
    return text, []


def on_hotkey_release():
    """Thin wrapper around _run_release_pipeline(): an uncaught exception in the
    pipeline would otherwise leave the widget stuck on PROCESSING (timer counting
    forever) and, for VAD-triggered calls, leave phase1_busy permanently set —
    silencing the wake word for the rest of the session. Reset everything instead."""
    try:
        _run_release_pipeline()
    except Exception as e:
        log.error(f"❌ Unexpected error in recording pipeline: {e}", exc_info=True)
        _clear_phase1_busy()
        _finish_command_recording()
        _disarm_vad_awake()
        _flash_error("UNEXPECTED ERROR", str(e)[:160])
        _arm_vad_cooldown()


def _run_release_pipeline():
    wav_bytes    = recorder.stop()
    duration_sec = round(time.time() - _record_start, 2)
    target_info  = tm.detect_target()

    _is_vad = _recording_source == "vad"
    _is_cmd = _vad_is_command_recording   # snapshot; will be cleared on exit

    if not wav_bytes:
        log.warning("No audio captured")
        if _is_cmd:
            _finish_command_recording()
            widget.hide()
        elif _is_vad:
            widget.hide()
        else:
            _flash_error("NO AUDIO CAPTURED", "Microphone returned no samples")
        _arm_vad_cooldown()
        return

    # ── RMS silence guard ─────────────────────────────────────
    rms = _wav_rms(wav_bytes)
    log.info(f"🔊 Audio RMS: {rms:.4f}")
    if rms < _RMS_SILENCE_THRESHOLD:
        global _near_silence_streak
        # Some signal but below the ASR threshold → mic is connected but the
        # Windows input level is too low (vs. ≈0 which is a muted/dead mic).
        if rms > 0.0006:
            _near_silence_streak += 1
        else:
            _near_silence_streak = 0
        _low_mic = _near_silence_streak >= 3
        log.warning(f"Audio is near-silence (RMS={rms:.4f}) — skipping ASR")
        if _near_silence_streak == 3:
            log.warning("⚠️  Mic is consistently too quiet — raise the input level "
                        "in Windows Sound settings (Microphone → Properties → "
                        "Levels) or move closer to the mic.")
        if _is_cmd:
            _finish_command_recording()
            widget.hide()
        elif _is_vad:
            # VAD path is normally silent on near-silence; surface the hint once,
            # only when we've crossed into the "consistently too quiet" state.
            if _near_silence_streak == 3:
                _flash_error("MIC TOO QUIET",
                             "Mic level very low — raise the input volume in "
                             "Windows Sound settings (Microphone → Levels)")
            else:
                widget.hide()
        elif _low_mic:
            _flash_error("MIC TOO QUIET",
                         f"Mic level too low (RMS {rms:.4f}) — raise the input "
                         f"volume in Windows Sound settings (Microphone → Levels)")
        else:
            _flash_error("SILENCE DETECTED",
                         f"Mic level too low (RMS {rms:.4f}) — check mic or speak louder")
        _arm_vad_cooldown()
        return

    # Clip passed the RMS gate — the mic is working at a usable level again.
    _near_silence_streak = 0

    # Phase 1 (sleeping): check for wake word.
    # Phase 2 (awake):    skip wake word check — user already confirmed it.
    # Hotkey sessions:    no wake word check at all.
    _ww_str = (getattr(config, "WAKE_WORD", "") or "").strip()
    _ww_enabled = getattr(config, "WAKE_WORD_ENABLED", False)

    is_vad_wake_phase = (
        _recording_source == "vad"
        and _ww_enabled
        and _ww_str
        and not _is_cmd   # Phase-2 command recordings bypass the wake-word check
    )

    # ── Phase-2 speaker verification ─────────────────────────────
    # A false-positive wake confirmation (e.g. a TV/video mishearing the wake
    # phrase) opens the command window, after which Phase 2 would otherwise
    # transcribe and PASTE whatever audio follows — including that same background
    # media. Gate Phase-2 audio on the enrolled voice too, so only the real user's
    # speech is ever pasted. No-op when no voice profile is enrolled (verify → accept)
    # or when the user has turned enrollment off via VOICE_ENROLLMENT_ENABLED.
    if _is_cmd and getattr(config, "VOICE_ENROLLMENT_ENABLED", True):
        try:
            import voice_enrollor as _ve
            _thr = getattr(config, "VOICE_SIMILARITY_THRESHOLD", 0.62)
            _ok, _score = _ve.verify(wav_bytes, _thr)
            if not _ok:
                log.info(f"Phase 2 speaker verify: rejected (score={_score:.2f}) — "
                         f"not the enrolled voice, discarding command")
                # Visible feedback so a rejected command isn't silently swallowed
                # (otherwise the wake word "just stops working" with no explanation).
                # Save a history row AND flash the widget with the score so the user
                # can tell a genuine self-rejection (score just under threshold) from
                # a real impostor, and knows to lower the threshold / re-enroll.
                db.save_transcription(
                    raw_text="", clean_text="",
                    duration_sec=duration_sec,
                    target_app=target_info["label"],
                    category=target_info["category"],
                    language=config.LANGUAGE,
                    status="speaker_rejected",
                    error=f"voice not recognized (score {_score:.2f} < {_thr})",
                )
                _finish_command_recording()
                # Only surface a visible/audible alert when the score is JUST below
                # the bar — that's almost certainly the real user on an off day and
                # they need to know why nothing pasted. A score far below threshold
                # is ambient media / a different speaker, so discard it silently to
                # avoid dinging on every background voice (the whole point of the gate).
                if _score >= _thr - 0.12:
                    _flash_error("VOICE NOT RECOGNIZED",
                                 f"Speaker check failed — score {_score:.2f} (need ≥ {_thr}). "
                                 f"If this was you, lower the voice match strictness in "
                                 f"Config → Wake Word, or re-enroll your voice.")
                else:
                    widget.hide()
                if voice_act is not None:
                    voice_act.set_cooldown(0.3)
                return
        except Exception as _e:
            log.debug(f"Phase 2 speaker verify skipped: {_e}")

    if not is_vad_wake_phase:
        # Hotkey sessions and Phase-2 VAD command recordings: show processing now
        state.set_state("processing")
        if tray: tray.set_processing()
        widget.show_processing()
        notifier.ding_stop()

    # ── Three-layer wake word validation (Phase 1 only) ──────────
    # Layer 1: audio duration — recording must contain enough speech to hold the phrase.
    if is_vad_wake_phase and duration_sec < 0.45:
        log.info(f"⏩ Wake L1: clip too short ({duration_sec:.2f}s) — skipping ASR")
        widget.hide()
        if voice_act is not None:
            voice_act.set_cooldown(0.5)
        return

    # Layer 1b: clips that ran to the max-duration cap are continuous ambient audio
    # (TV, music, background speech) — a genuine "hey voice" takes < 2 s.
    # Skip Groq entirely so media playing nearby doesn't waste API calls.
    if is_vad_wake_phase:
        global _ambient_clip_streak
        max_rec = max(1.0, getattr(config, "WAKE_WORD_MAX_RECORD_SEC", 3.5))
        if duration_sec >= max_rec - 0.3:
            _ambient_clip_streak += 1
            log.info(f"⏩ Wake L1b: clip hit {max_rec:.1f}s cap ({duration_sec:.2f}s) — "
                     f"ambient audio, skip ASR (streak #{_ambient_clip_streak})")
            # After 3 consecutive ambient clips: auto-raise threshold and persist it
            if _ambient_clip_streak >= 3 and _ambient_clip_streak % 3 == 0:
                ww_thr = getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.10)
                # Cap matches the engine ceiling in voice_activator.py — never
                # suggest or persist a value the VAD would refuse to honor. The
                # old cap (0.08) sat below loud-room ambient, so the auto-raise
                # never fired on the setups that needed it most and the log
                # advised threshold values the engine silently clamped away.
                _MAX_WAKE_THRESHOLD = getattr(config, "WAKE_WORD_VAD_THRESHOLD_MAX", 0.30)
                # Judge by the clip's PEAK window, not its average: the VAD gate
                # fires on a fast-attack EMA, so a clip averaging 0.09 can spike
                # past a 0.19 gate. An average-based bar (old: rms > thr*0.85)
                # never cleared on those clips, so the raise never happened and
                # the peaks kept triggering forever.
                peak = _wav_peak_rms(wav_bytes)
                if peak > ww_thr and ww_thr < _MAX_WAKE_THRESHOLD:
                    # Just above the peak that crossed the gate — enough to stop
                    # this source, without jumping straight to the cap.
                    new_thr = min(round(peak * 1.02, 2), _MAX_WAKE_THRESHOLD)
                    config.WAKE_WORD_VAD_THRESHOLD = new_thr
                    config.save()
                    log.warning(
                        f"⚠️  Background audio keeps triggering (avg {rms:.3f}, "
                        f"peak {peak:.3f} RMS). Auto-raised WAKE_WORD_VAD_THRESHOLD "
                        f"{ww_thr} → {new_thr} (cap {_MAX_WAKE_THRESHOLD}), saved to "
                        f"config.json. If the wake phrase stops being detected, use "
                        f"Config → Wake Phrase → Calibrate."
                    )
                else:
                    log.warning(
                        f"⚠️  Ambient audio streak ({_ambient_clip_streak}). "
                        f"threshold={ww_thr} (cap {_MAX_WAKE_THRESHOLD}), ambient avg "
                        f"{rms:.3f} / peak {peak:.3f} RMS. Mute background audio, or "
                        f"lower the microphone input level in Windows Sound settings — "
                        f"ambient this loud usually means mic gain is set high."
                    )
            widget.hide()
            if voice_act is not None:
                _clear_phase1_busy()
                voice_act.set_cooldown(0.5)
            return
        else:
            _ambient_clip_streak = 0   # normal-length clip resets the streak

    # Speaker verification (Phase 1 only, when a voice profile is enrolled and
    # enrollment is enabled). Check BEFORE setting phase1_busy so it's a fast,
    # local-only check.
    if is_vad_wake_phase and getattr(config, "VOICE_ENROLLMENT_ENABLED", True):
        try:
            import voice_enrollor as _ve
            _ok, _score = _ve.verify(wav_bytes, getattr(config, "VOICE_SIMILARITY_THRESHOLD", 0.62))
            if not _ok:
                log.info(f"Speaker verify: rejected (score={_score:.2f}) — not the enrolled voice")
                widget.hide()
                if voice_act is not None:
                    voice_act.set_cooldown(0.3)
                return
        except Exception as _e:
            log.debug(f"Speaker verify skipped: {_e}")

    # Mark Phase-1 pipeline as busy so no new VAD clip starts while Groq runs.
    if is_vad_wake_phase and voice_act is not None:
        global _phase1_busy_since
        _phase1_busy_since = time.time()
        voice_act.set_phase1_busy(True)

    # Phase 1: use the more accurate whisper-large-v3 model (not turbo) to reduce
    # short-clip mishears like "hey voice" → "the flow".
    # Phase 2 / hotkey: use the normal (faster) model with per-app vocabulary hint.
    _vad_ww      = _ww_str if is_vad_wake_phase else ""
    _wake_model  = getattr(config, "GROQ_ASR_MODEL_WAKE", "").strip() if is_vad_wake_phase else ""
    _base_prompt = getattr(config, "WHISPER_PROMPT", "").strip()

    if not is_vad_wake_phase:
        # Whisper prompt = the user's curated dictionary (WHISPER_PROMPT — names,
        # jargon, product terms) PLUS the vocabulary learned from past transcripts
        # for this app. These were previously `app_vocab or _base_prompt`, so once
        # an app had any history the user's curated terms were silently dropped —
        # the words they added specifically to fix mishears stopped applying.
        app_vocab = db.get_app_vocabulary(target_info.get("label", ""))
        # Whisper copies the prompt's punctuation, so a comma-separated list makes
        # it emit a comma after almost every word ("this, is, a, test"). Normalise
        # the dictionary to a space-separated word list (same as app_vocab). Put the
        # curated dictionary LAST so it survives Whisper's ~224-token prompt window
        # if the combined hint is long. Cap to keep well under that limit.
        _dict_ws = " ".join(_base_prompt.replace(",", " ").split())
        # Keep the TAIL (Whisper conditions on the last tokens of the prompt) so the
        # curated dictionary, placed last, is the part that survives if it's long.
        _combined_prompt = " ".join(p for p in (app_vocab, _dict_ws) if p)[-800:].strip()
        if app_vocab:
            log.info(f"📚 App vocab hint: {app_vocab[:80]}…")
    else:
        # Title-case the wake word for the Whisper prompt so it recognises it as a
        # proper noun/name, which significantly reduces phonetic substitutions.
        _combined_prompt = _vad_ww.title()

    try:
        raw_text = transcrib.transcribe(wav_bytes, initial_prompt=_combined_prompt,
                                        model_override=_wake_model)
    except Exception as e:
        log.error(f"❌ ASR exception: {e}")
        if is_vad_wake_phase and voice_act is not None:
            _clear_phase1_busy()
            voice_act.set_cooldown(0.8)
        if _is_cmd:
            # Phase-2 failure — always reset or VAD gets permanently stuck in command state
            _finish_command_recording()
            widget.hide()
        elif not is_vad_wake_phase:
            _flash_error("ASR ERROR", str(e))
        return

    # Layer 2: hallucination / empty transcript filter
    if raw_text and _is_hallucination(raw_text):
        log.warning(f"⏩ Wake check L2: ASR hallucination {raw_text!r} — discarding")
        raw_text = ""
    if not raw_text:
        if is_vad_wake_phase:
            # Wake-word-only users (no hotkey ever pressed) would otherwise never
            # see the "NO API KEY SET" notice — every Phase-1 clip silently returns
            # here, so the wake word looks permanently broken with zero feedback.
            global _asr_unconfigured_warned
            if not _asr_unconfigured_warned and not has_configured_backend():
                _asr_unconfigured_warned = True
                _clear_phase1_busy()
                _flash_error("NO API KEY SET",
                              "No AI transcription backend is configured. Open the "
                              "dashboard (http://localhost:7171) → Config → AI Backend "
                              "and add a free Groq API key.")
                if voice_act is not None:
                    voice_act.set_cooldown(0.8)
                return
            widget.hide()
            if voice_act is not None:
                _clear_phase1_busy()
                voice_act.set_cooldown(0.8)
            return
        # Non-wake path handles empty transcript below

    # Layer 3: wake word match (Phase 1 only)
    # ── Wake-word filter ──────────────────────────────────────
    if raw_text and is_vad_wake_phase:
        matched, remaining = _match_wake_word(raw_text, _ww_str)

        # Fallback: check user-defined phonetic alternatives (observed Whisper mishears).
        # e.g. WAKE_WORD_ALTERNATIVES = ["the flow"] catches "hey voice" → "the flow".
        if not matched:
            for alt in getattr(config, "WAKE_WORD_ALTERNATIVES", []):
                alt_matched, alt_remaining = _match_wake_word(raw_text, alt)
                if alt_matched:
                    matched   = True
                    remaining = alt_remaining
                    log.info(f"🔑 Wake word matched via alternative {alt!r}")
                    break

        if matched:
            log.info("🔑 Wake word confirmed")
            _ambient_clip_streak = 0   # genuine wake phrase resets ambient streak
            if voice_act is not None:
                _clear_phase1_busy()
            if remaining.strip():
                # Wake phrase + command in one breath — skip Phase 2 entirely,
                # process the remaining text directly (never set _vad_is_command_recording).
                log.info(f"   Command in same utterance: {remaining!r}")
                raw_text = remaining
                _disarm_vad_awake()
                if voice_act is not None:
                    voice_act.set_mode("sleep")   # no Phase 2 → go straight back to sleep
                state.set_state("processing")
                if tray: tray.set_processing()
                widget.show_processing()
                notifier.ding_stop()
            else:
                # Wake phrase only — arm command window, wait for next utterance
                _arm_vad_awake()
                db.save_transcription(
                    raw_text=raw_text, clean_text="",
                    duration_sec=duration_sec,
                    target_app=target_info["label"],
                    category=target_info["category"],
                    language=config.LANGUAGE,
                    status="wake_word",
                )
                return
        else:
            log.info("🔕 Wake word not found — silently discarding")
            if voice_act is not None:
                _clear_phase1_busy()
                voice_act.set_cooldown(0.4)   # short cooldown so user can retry quickly in noisy rooms
            db.save_transcription(
                raw_text=raw_text, clean_text="",
                duration_sec=duration_sec,
                target_app=target_info["label"],
                category=target_info["category"],
                language=config.LANGUAGE,
                status="discarded",
                error="wake word not found",
            )
            state.set_state("idle")
            widget.hide()
            return

    # Phase 2 ambient guard: a false-positive wake confirmation followed by
    # near-silence yields a short transcript that the user never intended.
    # If the audio is quiet AND the transcript is very short, discard it.
    if _is_cmd and raw_text:
        _word_count_raw = len(raw_text.split())
        _rms_low = rms < 0.018
        if _word_count_raw <= 2 and _rms_low:
            log.warning(f"⏩ Phase 2 ambient guard: {raw_text!r} "
                        f"({_word_count_raw} words, RMS={rms:.4f}) — discarding")
            _finish_command_recording()
            widget.hide()
            _arm_vad_cooldown()
            return

    if not raw_text:
        log.warning("Empty transcript")
        db.save_transcription(
            raw_text="", clean_text="",
            duration_sec=duration_sec,
            target_app=target_info["label"],
            category=target_info["category"],
            status="empty",
            error="no transcript",
        )

        # First empty result while no ASR backend is configured: show one loud,
        # actionable error regardless of path (hotkey, wake word, or command) —
        # otherwise VAD paths stay silent and the hotkey path shows a misleading
        # "no speech detected" on every single attempt.
        if not _asr_unconfigured_warned and not has_configured_backend():
            _asr_unconfigured_warned = True
            _flash_error("NO API KEY SET",
                          "No AI transcription backend is configured. Open the "
                          "dashboard (http://localhost:7171) → Config → AI Backend "
                          "and add a free Groq API key.")
            if _is_cmd:
                _finish_command_recording()
            widget.hide()
            _arm_vad_cooldown()
            return

        if _is_cmd:
            _finish_command_recording()
            widget.hide()
        elif _is_vad:
            widget.hide()
        else:
            _flash_error("NO SPEECH / ASR FAIL", "No speech detected or ASR failed")
        _arm_vad_cooldown()
        return

    clean_text = processor.clean(raw_text, llm_hint=target_info.get("llm_hint", ""))
    clean_text = _apply_rules(clean_text, target_info)

    # Snippet expansion: whole utterance matching a configured phrase → replacement text
    clean_text = _expand_snippets(clean_text)

    # Voice commands: "press enter" / "press tab" etc. stripped from end of text
    clean_text, post_keys = _apply_voice_commands(clean_text)

    if config.EDIT_BEFORE_PASTE:
        widget.hide()
        edited = edit_text_blocking(clean_text, target_label=target_info["label"])
        if edited is None:
            log.info("✋ User cancelled the paste")
            db.save_transcription(
                raw_text=raw_text, clean_text=clean_text,
                duration_sec=duration_sec,
                target_app=target_info["label"],
                category=target_info["category"],
                language=config.LANGUAGE,
                status="cancelled",
            )
            if _is_cmd:
                _finish_command_recording()
            state.set_state("idle")
            if tray: tray.set_idle()
            widget.hide()
            return
        clean_text = edited

    global _last_paste_text, _last_paste_chars
    log.info(f"📝 Pasting: '{clean_text}' → {target_info['label']}")
    injector.inject(clean_text)
    for key in post_keys:
        injector.press_key(key)
    _last_paste_text  = clean_text
    _last_paste_chars = len(clean_text)
    state.set_last_transcript(clean_text, target_info["label"])

    db.save_transcription(
        raw_text=raw_text,
        clean_text=clean_text,
        duration_sec=duration_sec,
        target_app=target_info["label"],
        category=target_info["category"],
        language=config.LANGUAGE,
        status="success",
    )

    state.set_state("idle")
    if tray: tray.set_idle()
    widget.hide()

    if _is_cmd:
        # Phase-2 command pasted — return VAD to sleep and suppress trailing speech
        _finish_command_recording()
        if voice_act is not None:
            voice_act.set_cooldown(2.0)
    elif _recording_source == "vad":
        _disarm_vad_awake()
        if voice_act is not None:
            voice_act.set_cooldown(2.0)
    elif voice_act is not None:
        voice_act.set_cooldown()


def on_command_press():
    """Command Mode: copy any selected text, then start recording an editing instruction."""
    global _selected_text, _command_start
    if recorder.is_recording:
        log.warning("Already recording — ignoring command hotkey")
        return
    target_info = tm.detect_target()
    log.info(f"🎯 Command target: {target_info['label']}")
    # Start recording FIRST so a quick key release never calls stop() before start().
    # copy_selection() takes ~0.18 s (Ctrl+C + clipboard read); recording runs in
    # the background during that window — the first 0.18 s is near-silence and the
    # user hasn't spoken yet, so transcription quality is unaffected.
    _command_start = time.time()
    recorder.start()
    state.set_state("recording")
    if tray: tray.set_recording()
    widget.show_recording()
    notifier.ding_start()
    # Grab selection while focus is still on the target app (user is holding the combo)
    _selected_text = injector.copy_selection()
    if _selected_text:
        log.info(f"📋 Selected text ({len(_selected_text)} chars)")
    else:
        log.info("📋 No selection — instruction will be treated as dictation")


def on_command_release():
    """Command Mode: transcribe the instruction, apply it to the selection, inject result."""
    wav_bytes    = recorder.stop()
    duration_sec = round(time.time() - _command_start, 2)
    target_info  = tm.detect_target()

    state.set_state("processing")
    if tray: tray.set_processing()
    widget.show_processing()
    notifier.ding_stop()

    if not wav_bytes:
        _flash_error("NO AUDIO", "Microphone returned no samples")
        return

    rms = _wav_rms(wav_bytes)
    if rms < _RMS_SILENCE_THRESHOLD:
        _flash_error("NO INSTRUCTION", "Speak your editing instruction")
        return

    instruction = transcrib.transcribe(wav_bytes)
    if not instruction or _is_hallucination(instruction):
        _flash_error("NO INSTRUCTION", "Could not hear the editing instruction")
        return

    log.info(f"🎙️  Command instruction: {instruction!r}")

    if _selected_text:
        result = processor.command(_selected_text, instruction)
        if not result:
            _flash_error("COMMAND FAILED", "LLM could not apply the instruction")
            return
        # Selected text is still highlighted — Ctrl+V replaces it
        injector.inject(result)
        state.set_last_transcript(result, target_info["label"])
        db.save_transcription(
            raw_text=instruction, clean_text=result,
            duration_sec=duration_sec,
            target_app=target_info["label"],
            category=target_info["category"],
            language=config.LANGUAGE,
            status="success",
        )
    else:
        # No selection — treat instruction as normal dictation
        clean_text = processor.clean(instruction, llm_hint=target_info.get("llm_hint", ""))
        clean_text = _apply_rules(clean_text, target_info)
        injector.inject(clean_text)
        state.set_last_transcript(clean_text, target_info["label"])
        db.save_transcription(
            raw_text=instruction, clean_text=clean_text,
            duration_sec=duration_sec,
            target_app=target_info["label"],
            category=target_info["category"],
            language=config.LANGUAGE,
            status="success",
        )

    state.set_state("idle")
    if tray: tray.set_idle()
    widget.hide()


def _apply_rules(text: str, rules: dict) -> str:
    if not text:
        return text
    if rules.get("capitalize", True):
        text = text[0].upper() + text[1:]
    else:
        text = text[0].lower() + text[1:]
    if not text:          # guard: LLM returned a single stripped char
        return text
    ends_with_punct = text[-1] in ".!?…"
    if rules.get("add_period", True) and not ends_with_punct:
        text += "."
    elif not rules.get("add_period", True) and text.endswith("."):
        text = text[:-1]
    return text


# ── Hotkey thread watchdog ───────────────────────────────────

def _start_hotkey(on_press, on_release,
                  on_cmd_press=None, on_cmd_release=None) -> threading.Thread:
    hl = HotkeyListener(
        on_press_cb=on_press,
        on_release_cb=on_release,
        on_command_press_cb=on_cmd_press,
        on_command_release_cb=on_cmd_release,
    )
    t  = threading.Thread(target=hl.start, daemon=True, name="HotkeyThread")
    t.start()
    return t


def _hotkey_watchdog(on_press, on_release,
                     on_cmd_press=None, on_cmd_release=None):
    """Restart HotkeyThread if it dies unexpectedly."""
    _t = [_start_hotkey(on_press, on_release, on_cmd_press, on_cmd_release)]

    def _watch():
        while not _shutdown_evt.is_set():
            time.sleep(8)
            if not _t[0].is_alive():
                log.warning("⚠️  HotkeyThread died — restarting")
                try:
                    _t[0] = _start_hotkey(on_press, on_release, on_cmd_press, on_cmd_release)
                    log.info("✅ HotkeyThread restarted")
                except Exception as e:
                    log.error(f"Hotkey restart failed: {e}")

    threading.Thread(target=_watch, daemon=True, name="HotkeyWatchdog").start()


# ── Undo last dictation ──────────────────────────────────────
# Sends Ctrl+Z to the active window to undo the last paste.
# Works in every Windows text editor, browser input, chat app, and IDE.

def on_undo_last():
    global _last_paste_text, _last_paste_chars
    if not _last_paste_chars:
        log.info("↩️  Undo: nothing to undo")
        return
    log.info(f"↩️  Undo last paste ({_last_paste_chars} chars): '{_last_paste_text[:40]}'")
    try:
        import pyautogui as _pg
        _pg.hotkey("ctrl", "z")
        log.info("↩️  Ctrl+Z sent")
    except Exception as e:
        log.warning(f"↩️  Undo failed: {e}")
    _last_paste_text  = ""
    _last_paste_chars = 0


# ── Dashboard window opener ──────────────────────────────────

def open_dashboard_window():
    _open_as_app_window("http://localhost:7171")


# ── Startup key check ────────────────────────────────────────

def _check_api_keys():
    """Warn only about keys that the active backends actually need."""
    asr_b = getattr(config, "ASR_BACKEND", "groq")
    llm_b = getattr(config, "LLM_BACKEND", "groq")

    if asr_b == "groq" and not getattr(config, "GROQ_API_KEY", ""):
        log.warning("⚠️  GROQ_API_KEY not set. Get a free key at console.groq.com")
        log.warning("   Open Config → AI Backend → Groq to add it.")
    elif asr_b == "openai" and not getattr(config, "OPENAI_API_KEY", ""):
        log.warning("⚠️  OPENAI_API_KEY not set for ASR backend 'openai'.")
    elif asr_b == "nvidia" and not config.NVIDIA_API_KEY:
        log.warning("⚠️  NVIDIA_API_KEY not set for ASR backend 'nvidia'.")

    if llm_b == "groq" and not getattr(config, "GROQ_API_KEY", ""):
        pass  # already warned above if ASR is also groq
    elif llm_b == "nvidia" and not config.NVIDIA_API_KEY:
        log.warning("⚠️  NVIDIA_API_KEY not set for LLM backend 'nvidia'.")
    elif llm_b == "openai" and not getattr(config, "OPENAI_API_KEY", ""):
        log.warning("⚠️  OPENAI_API_KEY not set for LLM backend 'openai'.")
    elif llm_b == "anthropic" and not getattr(config, "ANTHROPIC_API_KEY", ""):
        log.warning("⚠️  ANTHROPIC_API_KEY not set for LLM backend 'anthropic'.")


def apply_wake_word_setting() -> bool:
    """(Re)start or stop the VoiceActivator to match the current config.

    Safe to call at startup AND live from the dashboard, so toggling "wake word"
    in Config takes effect immediately instead of silently requiring a restart
    (the original behaviour — the listener was only ever created here at launch,
    so flipping the switch in the UI did nothing until the app was relaunched).

    Returns True if voice activation is active afterwards, False otherwise.
    """
    global voice_act

    wake_word    = (getattr(config, "WAKE_WORD", "") or "").strip()
    wake_enabled = getattr(config, "WAKE_WORD_ENABLED", False)
    want_active  = bool(wake_enabled and wake_word)

    if want_active and voice_act is None:
        from voice_activator import VoiceActivator
        voice_act = VoiceActivator(
            recorder=recorder,
            on_start_cb=on_vad_press,
            on_stop_cb=on_hotkey_release,
        )
        voice_act.set_mode("sleep")
        threading.Thread(target=voice_act.start, daemon=True,
                         name="VoiceActivator").start()
        ww_thr = getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.03)
        ww_sil = getattr(config, "WAKE_WORD_SILENCE_SEC",   0.70)
        log.info(f"Wake activation ON: '{wake_word}'  threshold={ww_thr}  silence={ww_sil}s")
    elif not want_active and voice_act is not None:
        try:
            voice_act.stop()      # detaches the monitor hook and closes the stream
        except Exception as e:
            log.warning(f"Wake activation stop error: {e}")
        voice_act = None
        # The shared stream is still needed for /api/mic-level calibration when
        # running hotkey-only, so reopen it now that the VAD released it.
        recorder.open_persistent_stream()
        log.info("Wake activation OFF — hotkeys only")
    elif wake_enabled and not wake_word:
        log.warning("Voice activation enabled but no wake phrase set — hotkeys only.")

    return voice_act is not None


# ── Entry point ──────────────────────────────────────────────

def main():
    global tray

    parser = argparse.ArgumentParser(description="NibCast")
    parser.add_argument("--minimized", action="store_true",
                        help="Start silently: no floating widget, no auto-open dashboard")
    args, _ = parser.parse_known_args()

    # --minimized flag wins over config, but config can also set START_MINIMIZED
    minimized = args.minimized or getattr(config, "START_MINIMIZED", False)

    _hk_configs = getattr(config, "HOTKEY_CONFIGS", [])
    _hk_lines = [f"{hc['combo']} [{hc.get('mode', 'hold')}]"
                 for hc in _hk_configs if isinstance(hc, dict) and hc.get("combo")]
    _voice_label = " + voice-activation" if getattr(config, "WAKE_WORD_ENABLED", False) else ""

    log.info("=" * 55)
    log.info(f"  NibCast v{__version__} — Starting")
    log.info(f"  Hotkeys  : {', '.join(_hk_lines) or ', '.join(config.HOTKEY_COMBOS)}")
    log.info(f"  Mode     : per-hotkey{_voice_label}")
    log.info(f"  ASR      : {getattr(config, 'ASR_BACKEND', 'groq').upper()}")
    log.info(f"  LLM      : {getattr(config, 'LLM_BACKEND', 'groq').upper()}")
    log.info("  Dashboard: http://localhost:7171")
    log.info(f"  Config   : {config.CONFIG_FILE}")
    if minimized:
        log.info("  Mode     : background (minimized)")
    log.info("=" * 55)

    _check_api_keys()

    # Apply history auto-delete if configured
    _auto_delete_days = getattr(config, "HISTORY_AUTO_DELETE_DAYS", 0)
    if _auto_delete_days > 0:
        try:
            db.delete_older_than(_auto_delete_days)
        except Exception:
            pass

    web_dashboard.start_dashboard()
    web_dashboard.set_widget_ref(widget)   # enables live style/shape changes from dashboard
    widget.set_icon_style(getattr(config, "WIDGET_STYLE", "wave"))
    widget.set_widget_shape(getattr(config, "WIDGET_SHAPE", "orb"))
    widget.set_theme(getattr(config, "WIDGET_THEME", "amber"))
    widget.start()

    widget.set_action_callbacks(
        on_start=on_hotkey_press,
        on_stop=on_hotkey_release,
        on_cancel=on_cancel_recording,
        on_dashboard=open_dashboard_window,
        on_settings=lambda: threading.Thread(
            target=settings.open, daemon=True).start(),
        on_quit=_do_quit,
    )

    if minimized:
        widget.hide()

    _hotkey_watchdog(
        on_hotkey_press, on_hotkey_release,
        on_cmd_press=on_command_press,
        on_cmd_release=on_command_release,
    )

    # ── Undo-last-dictation hotkey ──
    # Default: Ctrl+Alt+Z  — configurable via UNDO_HOTKEY in config.
    _undo_combo = getattr(config, "UNDO_HOTKEY", "<ctrl>+<alt>+z")
    if _undo_combo:
        try:
            from pynput import keyboard as _kb
            _undo_listener = _kb.GlobalHotKeys({_undo_combo: on_undo_last})
            _undo_listener.daemon = True
            _undo_listener.start()
            log.info(f"↩️  Undo hotkey registered: {_undo_combo}")
        except Exception as _e:
            log.warning(f"↩️  Undo hotkey failed to register: {_e}")

    # Phase-1 busy watchdog: if a Phase-1 ASR call hangs longer than the
    # HTTP timeout, reset the busy flag so new wake-word clips can be accepted.
    def _phase1_watchdog():
        global _phase1_busy_since
        _max_busy = getattr(config, "HTTP_TIMEOUT", 30) + 5
        while not _shutdown_evt.is_set():
            time.sleep(3)
            if (_phase1_busy_since > 0
                    and time.time() - _phase1_busy_since > _max_busy
                    and voice_act is not None):
                log.warning("Phase-1 busy watchdog: resetting stuck _phase1_busy flag")
                _clear_phase1_busy()
                _phase1_busy_since = 0.0
    threading.Thread(target=_phase1_watchdog, daemon=True, name="Phase1Watchdog").start()

    # Start the wake-word listener if it's enabled, and let the dashboard flip it
    # on/off live (without this hook, toggling it in Config did nothing until the
    # app was restarted).
    wake_active = apply_wake_word_setting()
    web_dashboard.set_wake_control(apply_wake_word_setting)

    # Open mic stream for level monitoring (needed by /api/mic-level).
    # VoiceActivator opens it automatically when wake word is enabled;
    # when using hotkeys only we open it here so calibration still works.
    if not wake_active:
        recorder.open_persistent_stream()

    # If the input device failed to open (bad INPUT_DEVICE index, mic
    # permission denied, device already in use by another app), the VAD
    # path would otherwise sit silently with no audio ever arriving and
    # hotkey presses would just show "NO AUDIO CAPTURED" with no clear
    # cause. Surface it once, after the stream has had a moment to open.
    def _check_mic_stream():
        if not recorder.stream_open:
            _flash_error("MIC NOT FOUND",
                          "Could not open the microphone. Check Windows mic "
                          "permissions (Settings → Privacy → Microphone) and "
                          "the Input Device in the dashboard's Config page.")
    threading.Timer(1.5, _check_mic_stream).start()

    if not minimized:
        threading.Timer(1.5, lambda: threading.Thread(
            target=open_dashboard_window, daemon=True).start()).start()

    tray = TrayUI(
        on_quit_cb=_do_quit,
        on_settings_cb=lambda: threading.Thread(
            target=settings.open, daemon=True).start(),
        on_dashboard_cb=lambda: threading.Thread(
            target=open_dashboard_window, daemon=True).start(),
        on_toggle_widget_cb=lambda: widget.toggle_visibility(),
    )

    if wake_active:
        _ww = (getattr(config, "WAKE_WORD", "") or "").strip()
        log.info(f"NibCast running — say '{_ww}' or use hotkeys")
    else:
        log.info(f"NibCast running — {' / '.join(config.HOTKEY_COMBOS)}")

    # Auto-update check — non-blocking, silent on failure
    def _check_update():
        try:
            import requests as _req
            resp = _req.get(
                "https://api.github.com/repos/NibCast/nibcast/releases/latest",
                timeout=6, headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                latest = resp.json().get("tag_name", "").lstrip("v")
                current_parts = [int(x) for x in __version__.split(".")]
                latest_parts  = [int(x) for x in latest.split(".") if x.isdigit()]
                if latest_parts and latest_parts > current_parts:
                    log.info(f"Update available: v{latest} (current v{__version__})")
                    state.set_update_available(latest)
        except Exception:
            pass
    _update_timer = threading.Timer(8.0, _check_update)
    _update_timer.daemon = True
    _update_timer.start()

    tray.start()


if __name__ == "__main__":
    main()
