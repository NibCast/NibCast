# ============================================================
#  NibCast — Floating Status Widget (Compact Orb)
# ============================================================
import tkinter as tk
import threading
import queue
import math
import time


# Theme palettes — idle accent colour, dark variant, border
_THEMES = {
    "amber":  {"accent": "#e8a525", "dark": "#5a3a08", "border": "#3a2d15"},
    "violet": {"accent": "#8b5cf6", "dark": "#2d1a5a", "border": "#3a2060"},
    "cyan":   {"accent": "#06b6d4", "dark": "#062030", "border": "#0a3040"},
}


class FloatingWidget:
    # ── Orb dimensions (original circular shape) ───────────────
    W_IDLE  = 58;  H_IDLE  = 58
    W_FULL  = 76;  H_FULL  = 76
    # ── Bar dimensions (horizontal pill) ───────────────────────
    W_BAR_IDLE = 200; H_BAR_IDLE = 38
    W_BAR_FULL = 240; H_BAR_FULL = 44
    # ── Chip dimensions (minimal floating dot-bar) ──────────────
    W_CHIP_IDLE = 62;  H_CHIP_IDLE = 22
    W_CHIP_FULL = 90;  H_CHIP_FULL = 22

    TRANSP  = "#010101"

    C_RED     = "#ff3838"
    C_CYAN    = "#25ffe0"
    C_TEAL    = "#00d4cc"
    C_BG_IDLE = "#1c1a18"
    C_BG_REC  = "#1f0808"
    C_BG_PROC = "#080f14"
    C_BG_LIST = "#1a1500"
    C_BG_AWAK = "#001a18"

    # Defaults (overridden by set_theme)
    C_AMBER   = "#e8a525"
    C_AMBER_D = "#5a3a08"
    C_BORDER  = "#3a2d15"

    def __init__(self):
        self._root    = None
        self._canvas  = None
        # All Tk calls MUST run on the thread that created the Tk root (the
        # FloatWidget thread). Public methods are called from the main thread,
        # the hotkey-release thread, the VAD thread, and the Flask dashboard
        # thread — calling self._root.after() directly from any of those raises
        # an intermittent "main thread is not in main loop" RuntimeError under
        # load. Instead, external callers enqueue a callable here and a pump
        # scheduled inside the mainloop (so it runs on the Tk thread) drains it.
        # The queue also buffers calls made before mainloop starts.
        self._cmd_queue = queue.Queue()
        self._state   = "idle"
        self._step    = 0
        self._anim_id = None
        self._error_msg     = ""
        self._error_hide_id = None
        self._icon_style    = "wave"   # "wave", "orbit", "pulse"
        self._widget_shape  = "orb"    # "orb", "bar", "chip"
        self._phase_start   = 0.0      # when current state was entered

        # Drag tracking (absolute screen coords so delta is accurate)
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_win_x   = 0
        self._drag_win_y   = 0
        self._drag_dist    = 0
        # Local (canvas-relative) coords of last click — used to hit-test stop button
        self._local_click_x = 0
        self._local_click_y = 0

        # Widget position
        self._pos_x = 0
        self._pos_y = 0

        self._click_recording  = False
        self._user_hidden      = False   # True when user deliberately hides via tray

        # Action callbacks
        self._on_start     = None
        self._on_stop      = None
        self._on_cancel    = None   # cancel recording without pasting
        self._on_dashboard = None
        self._on_settings  = None
        self._on_quit      = None

    # ── Public API ─────────────────────────────────────────────

    def set_action_callbacks(self, on_start=None, on_stop=None,
                             on_cancel=None, on_dashboard=None,
                             on_settings=None, on_quit=None):
        self._on_start     = on_start
        self._on_stop      = on_stop
        self._on_cancel    = on_cancel
        self._on_dashboard = on_dashboard
        self._on_settings  = on_settings
        self._on_quit      = on_quit

    def start(self):
        threading.Thread(target=self._mainloop, daemon=True,
                         name="FloatWidget").start()

    def _enqueue(self, fn):
        """Schedule a callable to run on the Tk thread. Safe from any thread.
        Buffers until the mainloop pump starts so early calls aren't lost."""
        self._cmd_queue.put(fn)

    def show_listening(self):
        """Show amber 'listening' state: VAD heard something, checking for wake word."""
        self._enqueue(self._enter_listening)

    def show_awake(self):
        """Show bright teal 'awake' state: wake word confirmed, ready for command."""
        self._enqueue(self._enter_awake)

    def show_recording(self):
        self._enqueue(self._enter_recording)

    def show_processing(self):
        self._enqueue(self._enter_processing)

    def show_error(self, msg: str = "ERROR"):
        self._error_msg = msg[:24]
        self._enqueue(self._enter_error)

    def hide(self):
        """Return to idle (always-visible compact orb)."""
        self._enqueue(self._enter_idle)

    # ── Tkinter main loop ──────────────────────────────────────

    def _mainloop(self):
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.wm_attributes("-topmost", True)
        self._root.wm_attributes("-transparentcolor", self.TRANSP)
        self._root.wm_attributes("-alpha", 0.96)
        self._root.configure(bg=self.TRANSP)

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        iw, ih = self._idle_size()
        fw, fh = self._full_size()
        # Bar anchors bottom-center; orb and chip anchor bottom-right
        if self._widget_shape == "bar":
            self._pos_x = (sw - iw) // 2
        else:
            self._pos_x = sw - iw - 24
        self._pos_y = sh - ih - 72

        self._root.geometry(f"{iw}x{ih}+{self._pos_x}+{self._pos_y}")

        self._canvas = tk.Canvas(
            self._root, width=fw, height=fh,
            bg=self.TRANSP, highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)

        self._canvas.bind("<ButtonPress-1>",  self._drag_start)
        self._canvas.bind("<B1-Motion>",       self._drag_move)
        self._canvas.bind("<ButtonRelease-1>", self._drag_release)
        self._canvas.bind("<ButtonPress-3>",   self._show_menu)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)

        self._enter_idle()
        # Pump runs on the Tk thread (scheduled here) and drains commands that
        # other threads enqueued — this is what makes the widget thread-safe.
        self._root.after(40, self._pump)
        self._root.mainloop()

    def _pump(self):
        """Drain queued commands on the Tk thread. Rescheduled every 40 ms."""
        try:
            while True:
                fn = self._cmd_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        if self._root:
            self._root.after(40, self._pump)

    # ── State transitions ──────────────────────────────────────

    def _cancel_error_hide(self):
        if self._error_hide_id is not None:
            try:
                self._root.after_cancel(self._error_hide_id)
            except Exception:
                pass
            self._error_hide_id = None

    def _enter_idle(self):
        self._cancel_error_hide()
        self._state = "idle"
        self._step  = 0
        self._click_recording = False
        self._stop_anim()
        iw, ih = self._idle_size()
        self._resize_to(iw, ih)
        if not self._user_hidden:
            self._root.deiconify()
        self._tick_idle()

    def _enter_listening(self):
        self._cancel_error_hide()
        self._state = "listening"
        self._step  = 0
        self._phase_start = time.time()
        self._stop_anim()
        iw, ih = self._idle_size()
        self._resize_to(iw, ih)
        self._root.deiconify()
        self._tick_listening()

    def _enter_awake(self):
        self._cancel_error_hide()
        self._state = "awake"
        self._step  = 0
        self._phase_start = time.time()
        self._stop_anim()
        fw, fh = self._full_size()
        self._resize_to(fw, fh)
        self._root.deiconify()
        self._tick_awake()

    def _enter_recording(self):
        self._cancel_error_hide()
        self._state = "recording"
        self._step  = 0
        self._phase_start = time.time()
        self._stop_anim()
        fw, fh = self._full_size()
        self._resize_to(fw, fh)
        self._root.deiconify()
        self._tick_recording()

    def _enter_processing(self):
        self._cancel_error_hide()
        self._state = "processing"
        self._step  = 0
        self._phase_start = time.time()
        self._stop_anim()
        iw, ih = self._idle_size()
        self._resize_to(iw, ih)
        self._root.deiconify()
        self._tick_processing()

    def _enter_error(self):
        self._cancel_error_hide()
        self._state = "error"
        self._step  = 0
        self._click_recording = False
        self._stop_anim()
        fw, fh = self._full_size()
        self._resize_to(fw, fh)
        self._root.deiconify()
        self._tick_error()
        self._error_hide_id = self._root.after(3000, self._enter_idle)

    # ── Animation ticks ────────────────────────────────────────

    def _tick_idle(self):
        if self._state != "idle":
            return
        self._draw_idle(self._step)
        self._step   += 1
        self._anim_id = self._root.after(60, self._tick_idle)

    def _tick_listening(self):
        if self._state != "listening":
            return
        self._draw_listening(self._step)
        self._step   += 1
        self._anim_id = self._root.after(50, self._tick_listening)

    def _tick_awake(self):
        if self._state != "awake":
            return
        self._draw_awake(self._step)
        self._step   += 1
        self._anim_id = self._root.after(40, self._tick_awake)

    def _tick_recording(self):
        if self._state != "recording":
            return
        self._draw_recording(self._step)
        self._step   += 1
        self._anim_id = self._root.after(36, self._tick_recording)

    def _tick_processing(self):
        if self._state != "processing":
            return
        self._draw_processing(self._step)
        self._step   += 1
        self._anim_id = self._root.after(36, self._tick_processing)

    def _tick_error(self):
        if self._state != "error":
            return
        self._draw_error(self._step)
        self._step   += 1
        self._anim_id = self._root.after(60, self._tick_error)

    def _stop_anim(self):
        if self._anim_id is not None:
            try:
                self._root.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

    # ── Shape helpers ──────────────────────────────────────────

    def _idle_size(self):
        if self._widget_shape == "bar":
            return self.W_BAR_IDLE, self.H_BAR_IDLE
        if self._widget_shape == "chip":
            return self.W_CHIP_IDLE, self.H_CHIP_IDLE
        return self.W_IDLE, self.H_IDLE

    def _full_size(self):
        if self._widget_shape == "bar":
            return self.W_BAR_FULL, self.H_BAR_FULL
        if self._widget_shape == "chip":
            return self.W_CHIP_FULL, self.H_CHIP_FULL
        return self.W_FULL, self.H_FULL

    # ── Resize ─────────────────────────────────────────────────

    def _resize_to(self, w, h):
        self._canvas.config(width=w, height=h)
        self._root.geometry(f"{w}x{h}+{self._pos_x}+{self._pos_y}")

    # ── NibCast icon drawing ──────────────────────────────────

    def set_icon_style(self, style: str):
        if style in ("wave", "orbit", "pulse"):
            self._icon_style = style

    def set_widget_shape(self, shape: str):
        if shape not in ("orb", "bar", "chip"):
            return
        self._widget_shape = shape
        if self._root:
            # Reposition bar to screen-centre; orb/chip to bottom-right
            if shape == "bar":
                sw = self._root.winfo_screenwidth()
                iw, _ = self._idle_size()
                self._pos_x = (sw - iw) // 2
            # Resize the window to new shape's dimensions and redraw
            self._enqueue(self._reenter_current_state)

    def _reenter_current_state(self):
        """Re-enter the current state so the window resizes to the new shape."""
        fn = {
            "idle":       self._enter_idle,
            "listening":  self._enter_listening,
            "awake":      self._enter_awake,
            "recording":  self._enter_recording,
            "processing": self._enter_processing,
            "error":      self._enter_error,
        }.get(self._state, self._enter_idle)
        fn()

    def set_theme(self, theme: str):
        t = _THEMES.get(theme, _THEMES["amber"])
        self.C_AMBER   = t["accent"]
        self.C_AMBER_D = t["dark"]
        self.C_BORDER  = t["border"]

    def toggle_visibility(self):
        if not self._root:
            return
        self._user_hidden = not self._user_hidden
        if self._user_hidden:
            self._enqueue(self._root.withdraw)
        else:
            self._enqueue(self._root.deiconify)

    def _draw_icon(self, c, cx, cy, color, lw=2, animate_step=None):
        """Dispatch to the selected icon style."""
        if self._icon_style == "orbit":
            self._draw_orbit_icon(c, cx, cy, color, lw, animate_step)
        elif self._icon_style == "pulse":
            self._draw_pulse_icon(c, cx, cy, color, lw, animate_step)
        else:
            self._draw_vf_icon(c, cx, cy, color, lw, animate_step)

    def _draw_vf_icon(self, c, cx, cy, color, lw=2, animate_step=None):
        """WAVE — 5 equalizer bars representing voice + flow."""
        if animate_step is not None:
            heights = [
                3 + int(8 * abs(math.sin(animate_step * 0.14 + i * 0.85)))
                for i in range(5)
            ]
        else:
            heights = [5, 9, 13, 9, 5]
        spacing = 4
        start_x = cx - (len(heights) - 1) * spacing / 2
        for i, h in enumerate(heights):
            x = start_x + i * spacing
            c.create_line(x, cy - h, x, cy + h,
                          fill=color, width=lw, capstyle="round")

    def _draw_orbit_icon(self, c, cx, cy, color, lw=2, animate_step=None):
        """ORBIT — ring with small orbiting dot (signal / connectivity)."""
        r = 9
        c.create_oval(cx-r, cy-r, cx+r, cy+r, outline=color, width=lw, fill="")
        angle = (animate_step * 0.12 if animate_step is not None else 0)
        dx = int(r * math.cos(angle))
        dy = int(r * math.sin(angle))
        dr = 3
        c.create_oval(cx+dx-dr, cy+dy-dr, cx+dx+dr, cy+dy+dr,
                      fill=color, outline="")

    def _draw_pulse_icon(self, c, cx, cy, color, lw=2, animate_step=None):
        """PULSE — expanding concentric rings (broadcast / activation)."""
        step = animate_step if animate_step is not None else 0
        for i, base_r in enumerate([4, 8, 12]):
            phase = (step * 0.08 + i * 0.7) % (2 * math.pi)
            r = base_r + int(2 * math.sin(phase))
            alpha = max(0, 1 - i * 0.3)
            if alpha > 0:
                c.create_oval(cx-r, cy-r, cx+r, cy+r,
                              outline=color, width=max(1, lw - i),
                              fill="", dash=(4, 3) if i > 0 else None)

    # ── Draw dispatchers (route to shape-specific methods) ────────

    def _draw_idle(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "idle")
        elif self._widget_shape == "chip": self._draw_chip(s, "idle")
        else:                              self._draw_orb_idle(s)

    def _draw_listening(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "listening")
        elif self._widget_shape == "chip": self._draw_chip(s, "listening")
        else:                              self._draw_orb_listening(s)

    def _draw_awake(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "awake")
        elif self._widget_shape == "chip": self._draw_chip(s, "awake")
        else:                              self._draw_orb_awake(s)

    def _draw_recording(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "recording")
        elif self._widget_shape == "chip": self._draw_chip(s, "recording")
        else:                              self._draw_orb_recording(s)

    def _draw_processing(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "processing")
        elif self._widget_shape == "chip": self._draw_chip(s, "processing")
        else:                              self._draw_orb_processing(s)

    def _draw_error(self, s):
        if self._widget_shape == "bar":   self._draw_bar(s, "error")
        elif self._widget_shape == "chip": self._draw_chip(s, "error")
        else:                              self._draw_orb_error(s)

    # ── ORB draw methods ──────────────────────────────────────────

    def _draw_orb_awake(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_FULL, self.H_FULL
        cx, cy = w // 2, h // 2
        r = 32

        # Three expanding teal sonar rings (fast, different from red recording rings)
        for ring_i in range(3):
            phase = (s * 7 + ring_i * 28) % 84
            ring_r = r + int(phase * 0.22)
            opacity = max(0, 84 - phase)
            if opacity > 8:
                ov = int(opacity * 1.8)
                rr = 0x00
                gg = min(255, 0x60 + ov)
                bb = min(255, 0x80 + ov // 2)
                c.create_oval(cx-ring_r, cy-ring_r, cx+ring_r, cy+ring_r,
                              outline=f"#{rr:02x}{gg:02x}{bb:02x}", width=1, fill="")

        # Main circle — dark teal background
        blink_on = (s // 6) % 2 == 0
        bg = self.C_BG_AWAK if blink_on else "#002220"
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=bg, outline=self.C_TEAL, width=2)

        # Rotating teal arc (indicates active / ready)
        angle = (s * 10) % 360
        c.create_arc(cx-22, cy-22, cx+22, cy+22,
                     start=angle, extent=240,
                     outline=self.C_TEAL, width=3, style="arc")

        # Animated icon in bright teal
        self._draw_icon(c, cx, cy - 2, self.C_TEAL, lw=2, animate_step=s)

        # "SPEAK" label — tells the user exactly what to do
        label = "SPEAK" if (s // 14) % 2 == 0 else "NOW"
        c.create_text(cx, cy + 22, text=label,
                      fill=self.C_TEAL, font=("Consolas", 7, "bold"),
                      anchor="center")

        # Cancel button (top-right corner — tap to dismiss without command)
        bx, by = cx + 22, cy - 22
        c.create_oval(bx-7, by-7, bx+7, by+7,
                      fill="#002a28", outline=self.C_TEAL, width=1)
        c.create_text(bx, by, text="✕", fill=self.C_TEAL,
                      font=("Segoe UI", 8), anchor="center")

    # ── LISTENING draw  (58 × 58, amber) ─────────────────────────

    def _draw_orb_listening(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_IDLE, self.H_IDLE
        cx, cy = w // 2, h // 2
        r = 26

        # Two expanding amber rings (staggered phase)
        for ring_i in range(2):
            phase = (s * 5 + ring_i * 30) % 60
            ring_r = r + int(phase * 0.28)
            opacity = max(0, 60 - phase)
            if opacity > 5:
                ov = int(opacity * 2)
                rr = min(255, 0x90 + ov)
                gg = min(255, 0x55 + ov // 2)
                bb = 0x10
                c.create_oval(cx-ring_r, cy-ring_r, cx+ring_r, cy+ring_r,
                              outline=f"#{rr:02x}{gg:02x}{bb:02x}", width=1, fill="")

        # Main circle — dark amber
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=self.C_BG_LIST, outline=self.C_AMBER, width=2)

        # Sweeping amber arc (rotates to indicate active listening)
        angle = (s * 6) % 360
        c.create_arc(cx-r+3, cy-r+3, cx+r-3, cy+r-3,
                     start=angle, extent=200,
                     outline=self.C_AMBER, width=2, style="arc")

        # Animated icon
        self._draw_icon(c, cx, cy - 1, self.C_AMBER, lw=2, animate_step=s)

    # ── IDLE draw  (58 × 58) ───────────────────────────────────

    def _draw_orb_idle(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_IDLE, self.H_IDLE
        cx, cy = w // 2, h // 2
        r = 26

        # Breathing outer ring (amber glow)
        pulse = 0.5 + 0.5 * math.sin(s * 0.08)
        pr = 28 + int(3 * pulse)   # 28 → 31
        ri = int(0x28 + pulse * 0x38)  # ring intensity
        rg = int(0x1e + pulse * 0x28)
        rb = int(0x08 + pulse * 0x06)
        ring_col = f"#{min(255,ri):02x}{min(255,rg):02x}{min(255,rb):02x}"
        c.create_oval(cx-pr, cy-pr, cx+pr, cy+pr,
                      outline=ring_col, width=1, fill="")

        # Main circle
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=self.C_BG_IDLE, outline=self.C_BORDER, width=1)

        # Amber accent arc (top 120°)
        c.create_arc(cx-r+2, cy-r+2, cx+r-2, cy+r-2,
                     start=30, extent=120,
                     outline=self.C_AMBER, width=2, style="arc")

        # NibCast icon
        self._draw_icon(c, cx, cy - 1, self.C_AMBER, lw=2)

    # ── RECORDING draw  (76 × 76) ─────────────────────────────

    def _draw_orb_recording(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_FULL, self.H_FULL
        cx, cy = w // 2, h // 2
        r = 32

        # Expanding sonar rings (3 rings at staggered phases)
        for ring_i in range(3):
            phase = (s * 5 + ring_i * 34) % 100
            ring_r = r + int(phase * 0.20)     # expand from r to ~r+20
            opacity = max(0, 100 - phase)
            ov = int(opacity * 0.45)
            rr = min(255, ov + 130)
            gg = min(255, int(ov * 0.15))
            bb = min(255, int(ov * 0.08))
            if opacity > 10:
                c.create_oval(cx-ring_r, cy-ring_r, cx+ring_r, cy+ring_r,
                              outline=f"#{rr:02x}{gg:02x}{bb:02x}", width=1, fill="")

        # Main circle — dark red background
        blink_on = (s // 5) % 2 == 0
        bg = self.C_BG_REC if not blink_on else "#260a0a"
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=bg, outline=self.C_RED, width=2)

        # Inner pulsing ring
        pulse = 0.5 + 0.5 * math.sin(s * 0.18)
        ir = int(20 + 6 * pulse)
        c.create_oval(cx-ir, cy-ir, cx+ir, cy+ir,
                      outline=self.C_RED, width=1, fill="")

        # NibCast icon (animated when recording)
        self._draw_icon(c, cx, cy - 2, "#ffffff", lw=2, animate_step=s)

        # Elapsed recording time (bottom centre)
        elapsed = int(time.time() - self._phase_start)
        mins, secs = divmod(elapsed, 60)
        timer_str = f"{mins}:{secs:02d}" if mins else f"{secs}s"
        c.create_text(cx, cy + 22, text=timer_str,
                      fill="#cc3333", font=("Consolas", 8, "bold"),
                      anchor="center")

        # Stop / cancel button (top-right corner) — click to cancel without pasting
        bx, by = cx + 22, cy - 22
        btn_bg = "#3a0808" if blink_on else "#2a0606"
        c.create_oval(bx-8, by-8, bx+8, by+8,
                      fill=btn_bg, outline=self.C_RED, width=1)
        c.create_rectangle(bx-4, by-4, bx+4, by+4,
                           fill=self.C_RED, outline="")

    # ── PROCESSING draw  (58 × 58) ────────────────────────────

    def _draw_orb_processing(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_IDLE, self.H_IDLE
        cx, cy = w // 2, h // 2
        r = 26

        # Main circle
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=self.C_BG_PROC, outline="#1a2a30", width=1)

        # Spinning arc (leading edge bright cyan, trailing dim)
        angle = (s * 8) % 360
        c.create_arc(cx-20, cy-20, cx+20, cy+20,
                     start=angle, extent=220,
                     outline=self.C_CYAN, width=3, style="arc")
        c.create_arc(cx-20, cy-20, cx+20, cy+20,
                     start=angle+220, extent=70,
                     outline="#0a5050", width=2, style="arc")

        # Centre dot
        c.create_oval(cx-3, cy-3, cx+3, cy+3,
                      fill=self.C_CYAN, outline="")

        # Elapsed processing time (below spinner)
        elapsed = int(time.time() - self._phase_start)
        c.create_text(cx, cy + 14, text=f"{elapsed}s",
                      fill=self.C_CYAN, font=("Consolas", 8),
                      anchor="center")

    # ── ERROR draw  (76 × 76) ─────────────────────────────────

    def _draw_orb_error(self, s):
        c = self._canvas
        c.delete("all")
        w, h = self.W_FULL, self.H_FULL
        cx, cy = w // 2, h // 2
        r = 32

        blink_on = (s // 4) % 2 == 0

        # Outer alert ring
        if blink_on:
            c.create_oval(cx-r-3, cy-r-3, cx+r+3, cy+r+3,
                          outline=self.C_RED, width=2, fill="")

        # Main circle
        bg = "#2a0606" if blink_on else "#1a0404"
        c.create_oval(cx-r, cy-r, cx+r, cy+r,
                      fill=bg, outline=self.C_RED, width=2)

        # ! symbol
        col = self.C_RED if blink_on else "#cc2020"
        c.create_text(cx, cy - 3, text="!",
                      fill=col, font=("Segoe UI", 22, "bold"),
                      anchor="center")

    # ── Drag ───────────────────────────────────────────────────

    def _drag_start(self, e):
        self._drag_start_x  = e.x_root
        self._drag_start_y  = e.y_root
        self._drag_win_x    = self._pos_x
        self._drag_win_y    = self._pos_y
        self._drag_dist     = 0
        self._local_click_x = e.x   # canvas-relative; used by stop-button hit-test
        self._local_click_y = e.y

    def _drag_move(self, e):
        dx = e.x_root - self._drag_start_x
        dy = e.y_root - self._drag_start_y
        self._drag_dist = abs(dx) + abs(dy)
        self._pos_x = self._drag_win_x + dx
        self._pos_y = self._drag_win_y + dy
        w = self._root.winfo_width()
        h = self._root.winfo_height()
        self._root.geometry(f"{w}x{h}+{self._pos_x}+{self._pos_y}")

    def _drag_release(self, e):
        if self._drag_dist < 5:
            self._handle_click()

    # ── Click / menu handlers ──────────────────────────────────

    def _in_corner_btn(self) -> bool:
        """Return True if the last click hit the cancel button.

        Each shape places the cancel button differently:
          orb  — top-right of circle: cx+22, cy-22
          bar  — right end of pill:   w - h//2 - 4, h//2
          chip — no dedicated cancel button (too small); returns False so body
                 click falls through to the stop handler instead
        """
        w = self._root.winfo_width()  if self._root else self.W_FULL
        h = self._root.winfo_height() if self._root else self.H_FULL

        if self._widget_shape == "chip":
            return False   # chip has no cancel btn; body click = stop

        if self._widget_shape == "bar":
            r  = h // 2
            bx = w - r - 4     # matches _draw_bar cancel-button position
            by = h // 2
            return (abs(self._local_click_x - bx) <= 10 and
                    abs(self._local_click_y - by) <= 10)

        # orb (default)
        cx, cy = w // 2, h // 2
        bx, by = cx + 22, cy - 22   # matches _draw_orb_recording / _draw_orb_awake
        return (abs(self._local_click_x - bx) <= 9 and
                abs(self._local_click_y - by) <= 9)

    def _handle_click(self):
        if self._state == "idle":
            self._click_recording = True
            if self._on_start:
                threading.Thread(target=self._on_start,
                                 daemon=True, name="ClickStart").start()

        elif self._state == "awake":
            # Click anywhere on the awake widget cancels the command window
            if self._on_cancel:
                threading.Thread(target=self._on_cancel,
                                 daemon=True, name="CancelAwake").start()

        elif self._state == "recording":
            # Corner stop-button: cancel without pasting (any recording mode)
            if self._in_corner_btn():
                self._click_recording = False
                if self._on_cancel:
                    threading.Thread(target=self._on_cancel,
                                     daemon=True, name="CancelRecording").start()
                return
            # Body click: stop + process — only for toggle/VAD (not hold-to-speak)
            try:
                import state as _st
                _is_toggle_session = _st.get_session_toggle()
            except Exception:
                _is_toggle_session = False
            if self._click_recording or _is_toggle_session:
                self._click_recording = False
                if self._on_stop:
                    threading.Thread(target=self._on_stop,
                                     daemon=True, name="ClickStop").start()

    def _on_double_click(self, e):
        if self._on_dashboard:
            threading.Thread(target=self._on_dashboard,
                             daemon=True, name="ClickDash").start()

    def _show_menu(self, e):
        menu = tk.Menu(
            self._root, tearoff=0,
            bg="#1a1a1a", fg="#e5e5e5",
            activebackground="#d4a742", activeforeground="#000000",
            borderwidth=0, relief="flat",
            font=("Segoe UI", 10),
        )
        if self._on_dashboard:
            menu.add_command(
                label="Open Dashboard",
                command=lambda: threading.Thread(
                    target=self._on_dashboard, daemon=True).start(),
            )
        if self._on_settings:
            menu.add_command(
                label="Settings",
                command=lambda: threading.Thread(
                    target=self._on_settings, daemon=True).start(),
            )
        menu.add_separator()
        menu.add_command(
            label="Quit",
            command=self._on_quit if self._on_quit else lambda: None,
        )
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    # ══════════════════════════════════════════════════════════
    # BAR WIDGET  (horizontal pill — 200×38 idle, 240×44 active)
    # ══════════════════════════════════════════════════════════
    # Layout: [● dot][waveform bars][label + timer][✕ btn]
    # The dot colour encodes state; the label is a short ALL-CAPS tag.

    _BAR_STATES = {
        "idle":       {"dot": "#e8a525", "bg": "#111111", "label": "NIBCAST",  "lc": "#e8a525"},
        "listening":  {"dot": "#e8a525", "bg": "#1a1500", "label": "LISTENING","lc": "#e8a525"},
        "awake":      {"dot": "#00d4cc", "bg": "#001a18", "label": "SPEAK",    "lc": "#00d4cc"},
        "recording":  {"dot": "#ff3838", "bg": "#1f0808", "label": "REC",      "lc": "#ff3838"},
        "processing": {"dot": "#25ffe0", "bg": "#080f14", "label": "THINKING", "lc": "#25ffe0"},
        "error":      {"dot": "#ff3838", "bg": "#2a0606", "label": "ERROR",    "lc": "#ff3838"},
    }

    def _draw_bar(self, s, state: str):
        c    = self._canvas
        c.delete("all")
        cfg  = self._BAR_STATES.get(state, self._BAR_STATES["idle"])
        w    = self._root.winfo_width()  if self._root else self.W_BAR_IDLE
        h    = self._root.winfo_height() if self._root else self.H_BAR_IDLE
        r    = h // 2   # corner radius → full pill

        # Background pill
        c.create_oval(0, 0, h, h, fill=cfg["bg"], outline="")
        c.create_oval(w-h, 0, w, h, fill=cfg["bg"], outline="")
        c.create_rectangle(h//2, 0, w-h//2, h, fill=cfg["bg"], outline="")
        # Subtle border
        c.create_arc(0, 0, h, h, start=90, extent=180, outline="#333", width=1, style="arc")
        c.create_arc(w-h, 0, w, h, start=270, extent=180, outline="#333", width=1, style="arc")
        c.create_line(h//2, 0, w-h//2, 0, fill="#333", width=1)
        c.create_line(h//2, h-1, w-h//2, h-1, fill="#333", width=1)

        # ── State dot (left) ──────────────────────────────────
        dot_x, dot_y = r, h // 2
        dot_r = max(4, h // 6)
        # Pulse ring for active states
        if state in ("recording", "awake", "listening"):
            pulse = 0.5 + 0.5 * math.sin(s * 0.18)
            pr = dot_r + 2 + int(3 * pulse)
            c.create_oval(dot_x-pr, dot_y-pr, dot_x+pr, dot_y+pr,
                          outline=cfg["dot"], width=1, fill="")
        c.create_oval(dot_x-dot_r, dot_y-dot_r, dot_x+dot_r, dot_y+dot_r,
                      fill=cfg["dot"], outline="")

        # ── Waveform bars (centre-left zone) ─────────────────
        bar_x0   = r + dot_r + 10
        bar_x1   = w - 80   # leave room for label on the right
        zone_w   = bar_x1 - bar_x0
        n        = 5
        bar_h_max = h * 0.55
        if state in ("recording", "awake", "listening"):
            heights = [
                int(bar_h_max * (0.35 + 0.65 * abs(math.sin(s * 0.14 + i * 0.85))))
                for i in range(n)
            ]
        elif state == "processing":
            heights = [int(bar_h_max * (0.3 + 0.7 * abs(math.sin(s * 0.10 + i * 0.6))))
                       for i in range(n)]
        else:
            heights = [int(bar_h_max * v) for v in (0.35, 0.65, 1.0, 0.65, 0.35)]

        bw   = max(zone_w // (n * 2 - 1), 2)
        gap  = bw
        bx   = bar_x0 + (zone_w - (n * bw + (n-1) * gap)) // 2
        cy_b = h // 2
        for i, bh in enumerate(heights):
            c.create_line(bx + i*(bw+gap) + bw//2, cy_b - bh//2,
                          bx + i*(bw+gap) + bw//2, cy_b + bh//2,
                          fill=cfg["dot"], width=bw, capstyle="round")

        # ── Label + timer (right zone) ────────────────────────
        label = cfg["label"]
        if state == "recording":
            elapsed = int(time.time() - self._phase_start)
            mm, ss  = divmod(elapsed, 60)
            label   = f"REC {mm}:{ss:02d}" if mm else f"REC {ss}s"
        elif state == "processing":
            elapsed = int(time.time() - self._phase_start)
            label   = f"{elapsed}s..."
        label_x = w - 68
        c.create_text(label_x, h // 2, text=label,
                      fill=cfg["lc"], font=("Consolas", 8, "bold"),
                      anchor="w")

        # ── Cancel button (far right, recording/awake only) ────
        if state in ("recording", "awake"):
            bx2 = w - r - 4
            by2 = h // 2
            c.create_oval(bx2-8, by2-8, bx2+8, by2+8,
                          fill="#2a0000" if state == "recording" else "#002a28",
                          outline=cfg["dot"], width=1)
            c.create_text(bx2, by2, text="x", fill=cfg["dot"],
                          font=("Consolas", 9, "bold"), anchor="center")

    # ══════════════════════════════════════════════════════════
    # CHIP WIDGET  (minimal 62×22 dot strip — always compact)
    # ══════════════════════════════════════════════════════════
    # Just a state-coloured dot + very short label. Minimum screen footprint.

    _CHIP_STATES = {
        "idle":       {"dot": "#e8a525", "bg": "#111111", "label": ""},
        "listening":  {"dot": "#e8a525", "bg": "#1a1500", "label": "..."},
        "awake":      {"dot": "#00d4cc", "bg": "#001a18", "label": "SPEAK"},
        "recording":  {"dot": "#ff3838", "bg": "#1f0808", "label": "REC"},
        "processing": {"dot": "#25ffe0", "bg": "#080f14", "label": "..."},
        "error":      {"dot": "#ff3838", "bg": "#2a0606", "label": "ERR"},
    }

    def _draw_chip(self, s, state: str):
        c    = self._canvas
        c.delete("all")
        cfg  = self._CHIP_STATES.get(state, self._CHIP_STATES["idle"])
        w    = self._root.winfo_width()  if self._root else self.W_CHIP_IDLE
        h    = self._root.winfo_height() if self._root else self.H_CHIP_IDLE
        r    = h // 2

        # Pill background
        c.create_oval(0, 0, h, h, fill=cfg["bg"], outline="")
        c.create_oval(w-h, 0, w, h, fill=cfg["bg"], outline="")
        c.create_rectangle(r, 0, w-r, h, fill=cfg["bg"], outline="")

        # Dot
        dot_x = r
        dot_y = r
        dot_r = max(3, r - 4)
        if state in ("recording", "awake"):
            blink = (s // 5) % 2 == 0
            clr   = cfg["dot"] if blink else "#333333"
        else:
            clr = cfg["dot"]
        c.create_oval(dot_x - dot_r, dot_y - dot_r,
                      dot_x + dot_r, dot_y + dot_r,
                      fill=clr, outline="")

        # Label / timer
        label = cfg["label"]
        if state == "recording":
            elapsed = int(time.time() - self._phase_start)
            label   = f"{elapsed}s"
        if label:
            c.create_text(r + dot_r + 6, r, text=label,
                          fill=cfg["dot"], font=("Consolas", 8, "bold"),
                          anchor="w")
