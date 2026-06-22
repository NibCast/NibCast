# ============================================================
#  NibCast — System Tray UI
# ============================================================
import threading
from PIL import Image, ImageDraw
import pystray
from logger import log
import config


def _create_icon(color: str, state: str = "idle") -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=14, fill=color)

    cx, cy = size // 2, size // 2

    if state == "recording":
        # Solid circle = recording indicator
        r = 12
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white")
    elif state == "processing":
        # Arc = processing spinner (static representation)
        draw.arc([cx - 12, cy - 12, cx + 12, cy + 12], start=0, end=270,
                 fill="white", width=4)
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill="white")
    else:
        # 5-bar voice waveform — same motif as dashboard logo
        bar_w   = 4
        spacing = 6
        heights = [6, 10, 16, 10, 6]
        total_w = len(heights) * bar_w + (len(heights) - 1) * (spacing - bar_w)
        x0 = cx - total_w // 2
        for i, h in enumerate(heights):
            x = x0 + i * spacing
            y_top = cy - h
            y_bot = cy + h
            r = bar_w // 2
            draw.rounded_rectangle([x, y_top, x + bar_w - 1, y_bot], radius=r, fill="white")

    return img


class TrayUI:
    COLOR_IDLE       = "#4338ca"   # indigo
    COLOR_RECORDING  = "#dc2626"   # crimson
    COLOR_PROCESSING = "#0284c7"   # sky blue

    TITLE_IDLE       = "NibCast — Ready"
    TITLE_RECORDING  = "NibCast — Recording…"
    TITLE_PROCESSING = "NibCast — Processing…"

    def __init__(self, on_quit_cb, on_settings_cb,
                 on_dashboard_cb=None, on_toggle_widget_cb=None):
        self._on_quit           = on_quit_cb
        self._on_settings       = on_settings_cb
        self._on_dashboard      = on_dashboard_cb
        self._on_toggle_widget  = on_toggle_widget_cb
        self._icon              = None
        self._widget_visible    = True

    def start(self):
        hk_label = " / ".join(config.HOTKEY_COMBOS) if config.HOTKEY_COMBOS else "—"

        menu = pystray.Menu(
            pystray.MenuItem("NibCast", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Hotkeys: {hk_label}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Dashboard",
                lambda icon, item: self._on_dashboard() if self._on_dashboard else None),
            pystray.MenuItem("Settings",
                lambda icon, item: self._on_settings()),
            pystray.MenuItem("Toggle Widget",
                lambda icon, item: self._toggle_widget()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self._quit()),
        )

        self._icon = pystray.Icon(
            name="NibCast",
            icon=_create_icon(self.COLOR_IDLE, "idle"),
            title=self.TITLE_IDLE,
            menu=menu,
        )

        log.info("System tray started")
        self._icon.run()

    def set_recording(self):
        self._update(self.COLOR_RECORDING, self.TITLE_RECORDING, "recording")

    def set_processing(self):
        self._update(self.COLOR_PROCESSING, self.TITLE_PROCESSING, "processing")

    def set_idle(self):
        self._update(self.COLOR_IDLE, self.TITLE_IDLE, "idle")

    def _update(self, color: str, title: str, state: str = "idle"):
        if not self._icon:
            return
        try:
            self._icon.icon  = _create_icon(color, state)
            self._icon.title = title
        except Exception as e:
            log.debug(f"tray icon update failed: {e}")

    def _toggle_widget(self):
        self._widget_visible = not self._widget_visible
        if self._on_toggle_widget:
            threading.Thread(target=self._on_toggle_widget, daemon=True).start()

    def _quit(self):
        log.info("Quitting NibCast")
        if self._icon:
            self._icon.stop()
        self._on_quit()
