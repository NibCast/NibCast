# ============================================================
#  NibCast — Text Injector
# ============================================================
#  Primary path: clipboard + simulated Ctrl+V (fast, preserves unicode).
#  Fallback   : pynput.keyboard.Controller().type(text)  — works in
#               apps that reject simulated paste (some games, RDP,
#               Citrix, certain Java/Electron text fields).
#  Preserves the user's original clipboard when PRESERVE_CLIPBOARD.
# ============================================================

import sys
import time
import threading

import pyperclip
import pyautogui

import config
from logger import log

# Optional fallback typist
try:
    from pynput.keyboard import Controller as _PynputController
    _typer = _PynputController()
    _HAS_PYNPUT_TYPER = True
except Exception:
    _typer = None
    _HAS_PYNPUT_TYPER = False


pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = False


class TextInjector:

    def press_key(self, key: str):
        """Simulate a single key press after text injection (e.g. 'enter', 'tab')."""
        try:
            time.sleep(0.05)
            pyautogui.press(key)
            log.info(f"⌨️  Key pressed: {key}")
        except Exception as e:
            log.error(f"❌ press_key({key!r}) failed: {e}")

    def copy_selection(self) -> str:
        """
        Simulate Ctrl+C and return whatever was selected.
        Restores an empty clipboard to the previous contents if nothing was selected.
        """
        saved = ""
        try:
            saved = pyperclip.paste()
        except Exception:
            pass
        try:
            pyperclip.copy("")
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.18)
            selected = pyperclip.paste()
        except Exception as e:
            log.error(f"❌ copy_selection failed: {e}")
            return ""
        if not selected:
            try:
                pyperclip.copy(saved)
            except Exception:
                pass
        return selected

    def inject(self, text: str):
        if not text:
            log.warning("Empty text — nothing to inject")
            return

        if config.APPEND_NEWLINE:
            text += "\n"

        log.info(f"📋 Injecting: {text!r}")

        prev = None
        if config.PRESERVE_CLIPBOARD:
            try:
                prev = pyperclip.paste()
            except pyperclip.PyperclipException:
                prev = None

        paste_ok = False
        try:
            pyperclip.copy(text)
            time.sleep(0.05)

            if sys.platform == "darwin":
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.hotkey("ctrl", "v")

            paste_ok = True
            log.info("✅ Text injected (clipboard path)")

        except pyperclip.PyperclipException as e:
            log.warning(f"⚠️ Clipboard unavailable, switching to type-fallback: {e}")
            prev = None   # nothing to restore
        except Exception as e:
            log.warning(f"⚠️ Paste failed, switching to type-fallback: {e}")

        # Fallback: type the text directly if clipboard paste didn't fire
        if not paste_ok:
            if _HAS_PYNPUT_TYPER:
                try:
                    # type() in pynput handles unicode by sending Unicode events
                    _typer.type(text)
                    log.info("✅ Text injected (pynput typing fallback)")
                except Exception as e:
                    log.error(f"❌ Typing fallback also failed: {e}")
            else:
                # Last-resort: pyautogui.typewrite, ASCII-only
                try:
                    pyautogui.typewrite(text, interval=0.005)
                    log.info("✅ Text injected (pyautogui typewrite fallback)")
                except Exception as e:
                    log.error(f"❌ All injection paths failed: {e}")

        # Restore the user's clipboard a beat later so the paste finishes first.
        if prev is not None:
            def _restore(value: str):
                try:
                    time.sleep(0.6)
                    pyperclip.copy(value)
                except Exception:
                    pass
            threading.Thread(target=_restore, args=(prev,), daemon=True).start()
