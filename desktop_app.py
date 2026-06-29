# ============================================================
#  NibCast — Native desktop window launcher (optional)
# ============================================================
#  Run instead of main.py to open the dashboard as a true native
#  window via pywebview.  Falls back to main.py's behavior (tray +
#  Chrome --app) if pywebview isn't installed.
# ============================================================

import sys
import threading
import time

import config
import web_dashboard
from logger import log
from main import (
    settings, widget,
    on_hotkey_press, on_hotkey_release, open_dashboard_window,
)
from hotkey_listener import HotkeyListener
from tray_ui import TrayUI


def main():
    # db.init_db() already ran during `from main import …` above.
    log.info("🎙️  NibCast (desktop window mode) starting…")
    log.info(f"  Hotkeys: {', '.join(config.HOTKEY_COMBOS)}")
    log.info(f"  Config : {config.CONFIG_FILE}")

    web_dashboard.start_dashboard()
    widget.start()

    hotkey = HotkeyListener(on_press_cb=on_hotkey_press, on_release_cb=on_hotkey_release)
    threading.Thread(target=hotkey.start, daemon=True, name="HotkeyThread").start()

    # Wait briefly so the Flask server is up before pywebview connects.
    time.sleep(1.0)

    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed — falling back to Chrome --app mode.")
        log.warning("  Install with:  pip install pywebview")
        threading.Timer(0.5, open_dashboard_window).start()

        tray = TrayUI(
            on_quit_cb=lambda: sys.exit(0),
            on_settings_cb=lambda: threading.Thread(target=settings.open, daemon=True).start(),
            on_dashboard_cb=lambda: threading.Thread(target=open_dashboard_window, daemon=True).start(),
        )
        tray.start()
        return

    # pywebview must run on the main thread; the tray therefore lives
    # on a daemon thread instead (pystray supports this on Windows).
    def _tray_run():
        tray = TrayUI(
            on_quit_cb=lambda: sys.exit(0),
            on_settings_cb=lambda: threading.Thread(target=settings.open, daemon=True).start(),
            on_dashboard_cb=lambda: None,   # window already open
        )
        tray.start()
    threading.Thread(target=_tray_run, daemon=True, name="TrayThread").start()

    # Frameless window + custom in-page titlebar (the dashboard reveals its
    # own window controls once `pywebviewready` fires). The brand block in the
    # header carries the `pywebview-drag-region` class so it acts as the drag
    # handle; easy_drag stays off so the rest of the UI remains interactive.
    win_api = _WindowApi()
    window = webview.create_window(
        title="NibCast",
        url="http://localhost:7171",
        width=1180, height=820, min_size=(900, 600),
        background_color="#0e0c0a",
        text_select=True,
        frameless=True,
        easy_drag=False,
        js_api=win_api,
    )
    win_api.window = window
    webview.start()
    log.info("👋 Desktop window closed")


class _WindowApi:
    """JS-callable bridge for the custom titlebar's minimize/maximize/close.

    Every call is guarded: pywebview's window methods vary slightly across
    versions, and a missing method must never crash the UI thread.
    """

    def __init__(self):
        self.window = None
        self._maximized = False

    def minimize(self):
        try:
            self.window.minimize()
        except Exception as e:
            log.debug(f"window minimize failed: {e}")

    def toggle_maximize(self):
        try:
            if self._maximized:
                self.window.restore()
            else:
                self.window.maximize()
            self._maximized = not self._maximized
        except Exception as e:
            log.debug(f"window maximize toggle failed: {e}")

    def close(self):
        try:
            self.window.destroy()
        except Exception as e:
            log.debug(f"window close failed: {e}")


if __name__ == "__main__":
    main()
