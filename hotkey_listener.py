# ============================================================
#  NibCast — Global Hotkey Listener
# ============================================================
#  Uses a raw pynput Listener + manual key-state tracking instead
#  of GlobalHotKeys.  GlobalHotKeys has two known Windows issues:
#    1. Ctrl+Shift combos are consumed by the language-switcher
#       hook before GlobalHotKeys sees them.
#    2. Single-key combos (F9) are missed when another app owns
#       them via RegisterHotKey.
#  The raw-listener approach sees every low-level keystroke.
#
#  Recording modes (RECORDING_MODE):
#    "hold"   → hold combo → record → release → paste
#    "toggle" → press → record → press again → paste
#    "voice"  → wake-word activates; hotkeys still work as "hold"
# ============================================================

import sys
import ctypes
import time
import threading
from pynput import keyboard
import config
import state
from logger import log

_IS_WINDOWS = sys.platform == "win32"

_TOGGLE_DEBOUNCE_SEC = 0.6   # ignore key-repeat/double-tap events within this window

# ── GetAsyncKeyState VK table ─────────────────────────────────
# Used to evict stale entries from self._pressed when Windows fails
# to deliver a WM_KEYUP (Alt+Tab, screen lock, fast modifier taps).
_VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "space": 0x20, "enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "scroll_lock": 0x91, "caps_lock": 0x14, "num_lock": 0x90,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},   # f1=0x70 … f24=0x87
}

def _key_actually_down(name: str) -> bool:
    """Return True if the physical key is currently pressed.
    Uses GetAsyncKeyState on Windows; conservatively returns True on other platforms
    (stale-key eviction is skipped, which is harmless but slightly less accurate)."""
    if not _IS_WINDOWS:
        return True
    vk = _VK.get(name)
    if vk is None:
        if len(name) == 1:
            if name.isalpha():
                vk = ord(name.upper())
            elif name.isdigit():
                vk = ord(name)
    if vk is None:
        return True   # unknown key — assume still held (conservative)
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return True   # API failure — assume still held


# ── Pynput special key names that use angle-bracket format ───
_PYNPUT_SPECIAL = frozenset({
    "ctrl", "shift", "alt", "space", "enter", "return", "tab",
    "backspace", "delete", "escape", "esc",
    "up", "down", "left", "right",
    "home", "end", "page_up", "page_down",
    "insert", "caps_lock", "num_lock", "scroll_lock",
    "print_screen", "pause", "cmd", "super", "menu",
    *(f"f{i}" for i in range(1, 25)),
})


def _normalize_combo(combo: str) -> str:
    """<ctrl>+<alt>+<v> → <ctrl>+<alt>+v  (letters don't use angle brackets)"""
    parts = []
    for tok in combo.split("+"):
        tok = tok.strip()
        if tok.startswith("<") and tok.endswith(">"):
            inner = tok[1:-1].lower()
            parts.append(f"<{inner}>" if inner in _PYNPUT_SPECIAL else inner)
        else:
            parts.append(tok)
    return "+".join(parts)


def _parse_combo_tokens(combo: str) -> frozenset:
    """'<ctrl>+<alt>+v' → frozenset({'ctrl', 'alt', 'v'})"""
    tokens = set()
    for tok in combo.split("+"):
        tok = tok.strip().lower().strip("<>")
        if tok:
            tokens.add(tok)
    return frozenset(tokens)


def _key_to_name(key) -> str:
    """Return a normalised name for a pynput key, modifier-independent."""
    try:
        if isinstance(key, keyboard.Key):
            name = key.name.lower()
            # Strip left/right variants: ctrl_l → ctrl, shift_r → shift
            for sfx in ("_l", "_r"):
                if name.endswith(sfx):
                    return name[:-2]
            return name
        if hasattr(key, "vk") and key.vk:
            vk = key.vk
            if 0x41 <= vk <= 0x5A:         # A–Z
                return chr(vk).lower()
            if 0x30 <= vk <= 0x39:          # 0–9
                return chr(vk)
        if hasattr(key, "char") and key.char:
            try:
                return key.char.lower()
            except Exception:
                pass
    except Exception:
        pass
    return ""


class HotkeyListener:
    def __init__(self, on_press_cb, on_release_cb,
                 on_command_press_cb=None, on_command_release_cb=None):
        self._on_press_cb           = on_press_cb
        self._on_release_cb         = on_release_cb
        self._on_command_press_cb   = on_command_press_cb
        self._on_command_release_cb = on_command_release_cb
        self._active                = False
        self._active_combo          = None
        self._session_is_hold       = False
        self._session_is_command    = False
        self._lock                  = threading.Lock()
        self._pressed               = set()   # names of currently-held keys
        self._listener              = None
        self._last_toggle_at        = {}      # combo → epoch time of last accepted toggle event

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        raw = list(config.HOTKEY_COMBOS or [])
        if not raw:
            log.warning("No hotkeys configured — open Settings to set one.")
            return

        # Build per-combo metadata: {combo: {"tokens": frozenset, "recordMode": "hold"|"toggle"}}
        hk_configs = {
            _normalize_combo(hc["combo"]): hc.get("mode", "hold")
            for hc in getattr(config, "HOTKEY_CONFIGS", [])
            if isinstance(hc, dict) and hc.get("combo")
        }
        self._combos = {}
        for r in raw:
            combo = _normalize_combo(r)
            tokens = _parse_combo_tokens(combo)
            record_mode = hk_configs.get(combo, "hold")
            self._combos[combo] = {"tokens": tokens, "recordMode": record_mode}
            log.info(f"  ✅ Registered: {combo}  [{record_mode}] → {set(tokens)}")

        # Pre-sort by descending token count so more-specific combos are always
        # checked first.  Computed once here so _on_press pays zero sort cost.
        self._sorted_combos = sorted(
            self._combos.items(),
            key=lambda kv: -len(kv[1]["tokens"]),
        )

        log.info(f"⌨️  Hotkey listener started — {len(self._combos)} combo(s)")

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        # Block this thread so the watchdog doesn't think it died
        self._listener.join()

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass

    # ── Raw key events ────────────────────────────────────────

    def _on_press(self, key):
        name = _key_to_name(key)
        if not name:
            return

        with self._lock:
            # Evict stale keys before adding the new one.
            # Windows can miss WM_KEYUP on Alt+Tab, screen lock, or fast taps,
            # leaving phantom modifiers in self._pressed that would falsely trigger combos.
            stale = {k for k in self._pressed if not _key_actually_down(k)}
            if stale:
                self._pressed -= stale
                log.debug(f"Evicted stale keys: {stale}")
            self._pressed.add(name)
            current = frozenset(self._pressed)

        # Use pre-sorted list (computed once in start()) — no per-keypress sort cost.
        # More-specific combos (more tokens) are always checked before sub-combos.
        for combo, cdata in self._sorted_combos:
            if cdata["tokens"] and cdata["tokens"].issubset(current):
                self._fire_press(combo)
                return

    def _on_release(self, key):
        name = _key_to_name(key)

        # Single lock: read combo_tokens, check, and clear active state atomically.
        # The original two-lock design had a race window between releasing the first
        # lock and re-acquiring it: a new press could set _active=True in that gap,
        # and our deferred clear would then silently kill the new session.
        is_command = False
        should_fire = False
        with self._lock:
            if name:
                self._pressed.discard(name)
            if not self._active or not self._active_combo or not self._session_is_hold:
                return
            cdata = self._combos.get(self._active_combo, {})
            combo_tokens = cdata.get("tokens", frozenset()) if isinstance(cdata, dict) else cdata
            if name and name in combo_tokens:
                is_command               = self._session_is_command
                self._active             = False
                self._active_combo       = None
                self._session_is_hold    = False
                self._session_is_command = False
                should_fire              = True

        if should_fire:
            if is_command:
                log.info(f"🔵 Command RELEASED ({name})")
                if self._on_command_release_cb:
                    threading.Thread(target=self._on_command_release_cb, daemon=True,
                                     name="CommandRelease").start()
            else:
                log.info(f"🟢 Hotkey RELEASED ({name})")
                threading.Thread(target=self._on_release_cb, daemon=True,
                                 name="HotkeyRelease").start()

    # ── Mode dispatch ─────────────────────────────────────────

    def _fire_press(self, combo):
        cdata = self._combos.get(combo, {})
        record_mode = cdata.get("recordMode", "hold") if isinstance(cdata, dict) else "hold"

        if record_mode == "command":
            with self._lock:
                if self._active:
                    return          # key-repeat guard: ignore while already recording
                self._active             = True
                self._active_combo       = combo
                self._session_is_hold    = True   # release detection uses this flag
                self._session_is_command = True
            state.set_session_toggle(False)
            log.info(f"🔵 Command PRESSED — {combo}")
            if self._on_command_press_cb:
                threading.Thread(target=self._on_command_press_cb, daemon=True,
                                 name="CommandPress").start()
            return

        is_hold = record_mode != "toggle"

        if is_hold:
            with self._lock:
                if self._active:
                    return          # key-repeat guard: ignore while already recording
                self._active             = True
                self._active_combo       = combo
                self._session_is_hold    = True
                self._session_is_command = False
            state.set_session_toggle(False)
            log.info(f"🔴 Hotkey PRESSED — {combo}")
            threading.Thread(target=self._on_press_cb, daemon=True,
                             name="HotkeyPress").start()
        else:
            # Tap-to-toggle.
            # The OS sends key-repeat keydown events when any key is held.
            # Without a debounce, holding Ctrl+Alt+Space fires toggle ON → OFF → ON
            # in milliseconds, making it appear broken.  We ignore events within
            # _TOGGLE_DEBOUNCE_SEC of the last accepted toggle on this combo.
            now = time.time()
            with self._lock:
                last = self._last_toggle_at.get(combo, 0.0)
                if now - last < _TOGGLE_DEBOUNCE_SEC:
                    return   # key-repeat or accidental double-tap — ignore
                self._last_toggle_at[combo] = now
                was_active               = self._active
                self._active             = not was_active
                self._active_combo       = combo if not was_active else None
                self._session_is_hold    = False
                self._session_is_command = False
            if not was_active:
                state.set_session_toggle(True)
                log.info(f"🔴 Toggle ON — {combo}")
                threading.Thread(target=self._on_press_cb, daemon=True,
                                 name="HotkeyToggleOn").start()
            else:
                state.set_session_toggle(False)
                log.info(f"🟢 Toggle OFF — {combo}")
                threading.Thread(target=self._on_release_cb, daemon=True,
                                 name="HotkeyToggleOff").start()
