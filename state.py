# ============================================================
#  NibCast — App-wide runtime state
# ============================================================
#  A tiny, threadsafe singleton observed by both the floating
#  widget and the dashboard polling loop. No business logic here.
# ============================================================

import threading
import time

_lock              = threading.Lock()
_state             = "idle"    # idle | recording | processing | error
_started_at        = 0.0
_last_text         = ""
_last_target       = ""
_last_error        = ""
_session_is_toggle = False     # True when current recording started by a toggle hotkey

# Session-level usage counters (reset on app restart)
_session_words    = 0
_session_count    = 0
_session_secs     = 0.0
_update_available = ""   # set to latest version string when a newer release is found


def set_state(new_state: str, error: str = ""):
    global _state, _started_at, _last_error, _session_is_toggle
    with _lock:
        _state = new_state
        if new_state == "recording":
            _started_at = time.time()
        if new_state in ("idle", "processing"):
            _session_is_toggle = False
        if new_state == "error":
            _last_error = error
        else:
            _last_error = ""


def set_session_toggle(is_toggle: bool):
    global _session_is_toggle
    with _lock:
        _session_is_toggle = is_toggle


def get_session_toggle() -> bool:
    with _lock:
        return _session_is_toggle


def set_last_transcript(text: str, target: str = ""):
    global _last_text, _last_target
    with _lock:
        _last_text   = text
        _last_target = target


def set_update_available(version: str):
    global _update_available
    with _lock:
        _update_available = version

def get_update_available() -> str:
    with _lock:
        return _update_available


def add_session_usage(words: int, duration_sec: float):
    """Called after each successful transcription to update session counters."""
    global _session_words, _session_count, _session_secs
    with _lock:
        _session_words += words
        _session_count += 1
        _session_secs  += duration_sec


def snapshot() -> dict:
    with _lock:
        elapsed = (time.time() - _started_at) if _state == "recording" else 0.0
        return {
            "state":       _state,
            "elapsed_sec": round(elapsed, 2),
            "last_text":   _last_text,
            "last_target": _last_target,
            "last_error":  _last_error,
            "ts":          time.time(),
        }
