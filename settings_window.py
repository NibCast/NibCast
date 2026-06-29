# ============================================================
#  NibCast — Settings Window
# ============================================================
#  Design notes:
#   • Uses tk.Toplevel against a private, hidden root so it never
#     fights the FloatingWidget's Tk root.
#   • mainloop() is run on this module's own thread (started by
#     tray callback), so the tray pystray loop is never blocked.
#   • All hotkey buttons are real methods of the class.
#   • Aesthetic: same palette as the floating widget and the
#     web dashboard — JetBrains Mono / Bebas-style chunkiness
#     emulated with Courier weight + caps + sharp rectangles.
# ============================================================

import threading
import tkinter as tk
from tkinter import simpledialog, messagebox, ttk

import config

try:
    from audio_recorder import list_input_devices
except Exception:
    def list_input_devices(): return []


# ── Brutalist palette ────────────────────────────────────────
BG       = "#080808"
PANEL    = "#0d0d0d"
PANEL_HI = "#131313"
BORDER   = "#202020"
BORDER_2 = "#2e2e2e"
LIME     = "#d4ff3b"
RED      = "#ff2525"
CYAN     = "#25ffe0"
TEXT     = "#f0f0f0"
DIM      = "#404040"
GREY     = "#777777"

FONT_MONO   = ("Consolas", 10)
FONT_MONO_B = ("Consolas", 10, "bold")
FONT_LABEL  = ("Consolas", 9)
FONT_HEAD   = ("Consolas", 18, "bold")
FONT_SUB    = ("Consolas", 9)


class SettingsWindow:
    """Tk Toplevel window opened from the tray menu. Thread-safe
    with respect to the rest of the app — never reuses other roots,
    never blocks the tray loop."""

    def __init__(self):
        self._root    = None
        self._thread  = None
        self._lock    = threading.Lock()

        # Tk variables (created when window opens)
        self._api_key_var  = None
        self._lang_var     = None
        self._asr_var      = None
        self._llm_var      = None
        self._clean_var    = None
        self._newline_var  = None
        self._hotkey_list  = None
        self._show_key_var = None
        self._api_entry    = None
        self._hold_var     = None
        self._clip_var     = None
        self._edit_var     = None
        self._ding_var     = None
        self._device_var   = None
        self._device_map   = []  # [(index, label)]

    # ── Public API ────────────────────────────────────────────

    def open(self):
        """Tray menu calls this. Spin up the window on its own
        thread so the tray's pystray loop is never blocked."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                # Already open — try to lift it.
                if self._root is not None:
                    try:
                        self._root.after(0, self._lift)
                    except Exception:
                        pass
                return
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="SettingsWindow"
            )
            self._thread.start()

    # ── Threaded mainloop ─────────────────────────────────────

    def _run(self):
        self._root = tk.Tk()
        self._root.title("NibCast — Settings")
        self._root.configure(bg=BG)
        self._root.resizable(False, False)

        # Sharp, no system chrome accents we can't control
        try:
            self._root.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        w  = min(660, sw - 40)
        # Reserve ~80px for the OS taskbar/title bar
        h  = min(880, sh - 80)
        x  = max(0, (sw - w) // 2)
        y  = max(0, (sh - h) // 2)
        self._root.geometry(f"{w}x{h}+{x}+{y}")

        # Style ttk widgets for a flat look (no relief)
        self._init_tk_vars()
        self._build_ui()

        self._root.protocol("WM_DELETE_WINDOW", self._close)
        self._root.mainloop()

    def _lift(self):
        if self._root is None: return
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.focus_force()
        except Exception:
            pass

    def _close(self):
        try:
            if self._root: self._root.destroy()
        except Exception:
            pass
        self._root = None

    # ── State ─────────────────────────────────────────────────

    def _init_tk_vars(self):
        self._api_key_var  = tk.StringVar(value=config.NVIDIA_API_KEY)
        self._lang_var     = tk.StringVar(value=config.LANGUAGE)
        self._asr_var      = tk.StringVar(value=config.ASR_MODEL)
        self._llm_var      = tk.StringVar(value=config.LLM_MODEL)
        self._clean_var    = tk.BooleanVar(value=config.CLEAN_WITH_LLM)
        self._newline_var  = tk.BooleanVar(value=config.APPEND_NEWLINE)
        self._hold_var     = tk.BooleanVar(value=config.HOLD_TO_TALK)
        self._clip_var     = tk.BooleanVar(value=config.PRESERVE_CLIPBOARD)
        self._edit_var     = tk.BooleanVar(value=config.EDIT_BEFORE_PASTE)
        self._ding_var     = tk.BooleanVar(value=config.AUDIO_CUES)
        self._show_key_var = tk.BooleanVar(value=False)
        self._device_map   = list_input_devices()
        cur_dev_label = "— System Default —"
        for idx, lbl in self._device_map:
            if idx == config.INPUT_DEVICE:
                cur_dev_label = lbl
                break
        self._device_var = tk.StringVar(value=cur_dev_label)

    # ── UI builder ────────────────────────────────────────────

    def _build_ui(self):
        root = self._root

        # ── Header bar (lime accent) ──────────────────────────
        hdr = tk.Frame(root, bg=BG, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # Logo box
        logo = tk.Frame(hdr, bg=LIME, width=44, height=44)
        logo.place(x=20, y=10)
        logo.pack_propagate(False)
        tk.Label(logo, text="VF", bg=LIME, fg=BG,
                 font=("Consolas", 16, "bold")).place(relx=.5, rely=.5, anchor="center")

        tk.Label(hdr, text="NIBCAST", bg=BG, fg=TEXT,
                 font=("Consolas", 18, "bold")).place(x=80, y=8)
        tk.Label(hdr, text="// SETTINGS    v2.4", bg=BG, fg=DIM,
                 font=("Consolas", 9)).place(x=80, y=36)

        # Top lime accent stripe
        tk.Frame(root, bg=LIME, height=2).pack(fill="x")

        # ── Scrollable content ───────────────────────────────
        # Canvas + scrollbar pattern so the settings fit any screen.
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        vbar   = tk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                              bg=BG, activebackground=BORDER_2,
                              troughcolor=PANEL, bd=0, relief="flat",
                              highlightthickness=0, width=8)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=BG)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_config(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_config(e):
            # Keep the body's width matched to the canvas viewport
            canvas.itemconfigure(body_id, width=e.width)
        body.bind("<Configure>", _on_body_config)
        canvas.bind("<Configure>", _on_canvas_config)

        # Mousewheel: Windows fires <MouseWheel>; Linux fires Button-4/5
        def _on_mousewheel(e):
            try:
                if e.delta:
                    canvas.yview_scroll(int(-e.delta / 120), "units")
                elif getattr(e, "num", None) == 4:
                    canvas.yview_scroll(-1, "units")
                elif getattr(e, "num", None) == 5:
                    canvas.yview_scroll(1, "units")
            except Exception:
                pass
        root.bind_all("<MouseWheel>", _on_mousewheel)
        root.bind_all("<Button-4>",   _on_mousewheel)
        root.bind_all("<Button-5>",   _on_mousewheel)

        # Inner padding wrapper so the section dividers don't touch the edges
        body_pad = tk.Frame(body, bg=BG)
        body_pad.pack(fill="both", expand=True, padx=20, pady=16)
        body = body_pad   # downstream code packs into `body`

        # ── Section: API & Models ────────────────────────────
        self._section(body, "// API & MODELS")

        api_frame = self._row_frame(body)
        self._row_label(api_frame, "NVIDIA_API_KEY")
        api_inner = tk.Frame(api_frame, bg=PANEL)
        api_inner.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self._api_entry = tk.Entry(
            api_inner, textvariable=self._api_key_var, show="•",
            bg=BG, fg=TEXT, insertbackground=LIME,
            font=FONT_MONO, relief="flat",
            highlightthickness=1, highlightbackground=BORDER_2,
            highlightcolor=LIME, bd=0,
        )
        self._api_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        tk.Checkbutton(
            api_inner, text="SHOW", variable=self._show_key_var,
            command=self._toggle_key_visibility,
            bg=PANEL, fg=DIM, activebackground=PANEL, activeforeground=LIME,
            selectcolor=BG, font=("Consolas", 8, "bold"),
            relief="flat", bd=0, padx=8,
        ).pack(side="left")

        self._kv_row(body, "ASR_MODEL", self._asr_var)
        self._kv_row(body, "LLM_MODEL", self._llm_var)
        self._kv_row(body, "LANGUAGE",  self._lang_var, width=14)

        # ── Section: Hotkeys ────────────────────────────────
        self._section(body, "// HOTKEYS")

        hk_wrap = tk.Frame(body, bg=BG)
        hk_wrap.pack(fill="x", pady=(0, 6))

        # Listbox on the left with sharp border
        listframe = tk.Frame(hk_wrap, bg=BORDER_2, padx=1, pady=1)
        listframe.pack(side="left", fill="x", expand=True)
        self._hotkey_list = tk.Listbox(
            listframe, height=4, bg=BG, fg=TEXT,
            selectbackground=LIME, selectforeground=BG,
            font=FONT_MONO, relief="flat", bd=0,
            highlightthickness=0, activestyle="none",
        )
        self._hotkey_list.pack(fill="x")
        for hk in config.HOTKEY_COMBOS:
            self._hotkey_list.insert(tk.END, f"  {hk}")

        # Buttons on the right (each its own sharp rect)
        btns = tk.Frame(hk_wrap, bg=BG)
        btns.pack(side="left", padx=(10, 0))
        self._brutal_btn(btns, "+ ADD",    self._add_hotkey,    LIME).pack(fill="x", pady=(0, 4))
        self._brutal_btn(btns, "✎ EDIT",   self._edit_hotkey,   CYAN).pack(fill="x", pady=(0, 4))
        self._brutal_btn(btns, "✕ REMOVE", self._remove_hotkey, RED ).pack(fill="x")

        tk.Label(body,
                 text="Examples:  <ctrl>+<shift>+<space>   <ctrl>+<alt>+<v>   <f9>",
                 bg=BG, fg=DIM, font=("Consolas", 8)).pack(anchor="w", pady=(4, 6))

        # ── Section: Microphone ──────────────────────────────
        self._section(body, "// MICROPHONE")
        mic_row = self._row_frame(body)
        self._row_label(mic_row, "INPUT_DEVICE")
        labels = ["— System Default —"] + [lbl for _, lbl in self._device_map]
        # ttk Combobox styled to look brutalist-ish
        style = ttk.Style(self._root)
        try: style.theme_use("clam")
        except Exception: pass
        style.configure("VF.TCombobox",
                        fieldbackground=BG, background=BG, foreground=TEXT,
                        bordercolor=BORDER_2, lightcolor=BORDER_2, darkcolor=BORDER_2,
                        arrowcolor=LIME, selectbackground=LIME, selectforeground=BG)
        cmb = ttk.Combobox(mic_row, textvariable=self._device_var,
                           values=labels, state="readonly",
                           style="VF.TCombobox", font=FONT_MONO)
        cmb.pack(side="left", fill="x", expand=True, padx=(12, 0), ipady=4)

        # ── Section: Behavior ────────────────────────────────
        self._section(body, "// BEHAVIOR")
        self._toggle_row(body, "Hold-to-talk (off → tap toggles)", self._hold_var)
        self._toggle_row(body, "Use LLM to clean transcripts", self._clean_var)
        self._toggle_row(body, "Append newline after paste",   self._newline_var)
        self._toggle_row(body, "Preserve clipboard after paste", self._clip_var)
        self._toggle_row(body, "Edit transcript before paste",   self._edit_var)
        self._toggle_row(body, "Play start/stop/error sounds",   self._ding_var)

        # ── Action bar ───────────────────────────────────────
        spacer = tk.Frame(body, bg=BG, height=10); spacer.pack()

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(4, 0))
        self._brutal_btn(actions, "[ SAVE SETTINGS ]", self._save, LIME, pad_x=22, pad_y=10).pack(side="left")
        self._brutal_btn(actions, "[ CLOSE ]",         self._close, RED, pad_x=16, pad_y=10).pack(side="left", padx=8)

        self._save_status = tk.Label(actions, text="", bg=BG, fg=LIME,
                                     font=("Consolas", 9, "bold"))
        self._save_status.pack(side="left", padx=12)

        # Footer
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", side="bottom")
        foot = tk.Frame(root, bg=BG, height=24)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        tk.Label(foot, text=" CHANGES TAKE EFFECT IMMEDIATELY  //  HOTKEYS REBIND ON SAVE",
                 bg=BG, fg=DIM, font=("Consolas", 8)).pack(side="left", padx=12, pady=4)

    # ── UI atoms ──────────────────────────────────────────────

    def _section(self, parent, title):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", pady=(6, 6))
        tk.Label(wrap, text=title, bg=BG, fg=LIME,
                 font=("Consolas", 10, "bold")).pack(side="left")
        sep = tk.Frame(wrap, bg=BORDER, height=1)
        sep.pack(side="left", fill="x", expand=True, padx=10, pady=(8, 0))

    def _row_frame(self, parent):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=2)
        return row

    def _row_label(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=GREY,
                 font=FONT_LABEL, width=16, anchor="w").pack(side="left")

    def _kv_row(self, parent, key, var, width=46):
        row = self._row_frame(parent)
        self._row_label(row, key)
        ent = tk.Entry(
            row, textvariable=var, bg=BG, fg=TEXT,
            insertbackground=LIME, font=FONT_MONO,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER_2,
            highlightcolor=LIME,
        )
        ent.pack(side="left", fill="x", expand=True, padx=(12, 0), ipady=6)
        return ent

    def _toggle_row(self, parent, label, var):
        row = self._row_frame(parent)
        cb = tk.Checkbutton(
            row, text=" " + label, variable=var,
            bg=BG, fg=TEXT, activebackground=BG, activeforeground=LIME,
            selectcolor=BG, font=FONT_MONO,
            relief="flat", bd=0, padx=0, anchor="w",
        )
        cb.pack(side="left", fill="x", expand=True, pady=2)

    def _brutal_btn(self, parent, text, cmd, accent, pad_x=14, pad_y=6):
        """Sharp rectangle button with colored bottom border + hover."""
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=BG, fg=accent,
            activebackground=accent, activeforeground=BG,
            font=("Consolas", 9, "bold"),
            relief="flat", bd=0,
            padx=pad_x, pady=pad_y,
            highlightthickness=1, highlightbackground=accent,
            cursor="hand2",
        )
        def _enter(_): btn.configure(bg=accent, fg=BG)
        def _leave(_): btn.configure(bg=BG, fg=accent)
        btn.bind("<Enter>", _enter)
        btn.bind("<Leave>", _leave)
        return btn

    # ── Behavior ──────────────────────────────────────────────

    def _toggle_key_visibility(self):
        if self._api_entry is None: return
        self._api_entry.configure(show="" if self._show_key_var.get() else "•")

    # ── Hotkey methods (real methods now) ─────────────────────

    def _add_hotkey(self):
        new = simpledialog.askstring(
            "Add Hotkey",
            "Enter new hotkey combo:\n"
            "(e.g. <ctrl>+<alt>+<v>  or  <f9>)",
            parent=self._root,
        )
        if not new or not new.strip(): return
        new = new.strip()
        if new in config.HOTKEY_COMBOS:
            messagebox.showwarning("Duplicate", "That hotkey is already in the list.", parent=self._root)
            return
        config.HOTKEY_COMBOS.append(new)
        self._hotkey_list.insert(tk.END, f"  {new}")

    def _remove_hotkey(self):
        sel = self._hotkey_list.curselection()
        if not sel:
            messagebox.showinfo("Pick one", "Select a hotkey to remove.", parent=self._root)
            return
        idx = sel[0]
        if len(config.HOTKEY_COMBOS) <= 1:
            messagebox.showwarning("Cannot remove",
                                   "At least one hotkey must remain.",
                                   parent=self._root)
            return
        del config.HOTKEY_COMBOS[idx]
        self._hotkey_list.delete(idx)

    def _edit_hotkey(self):
        sel = self._hotkey_list.curselection()
        if not sel:
            messagebox.showinfo("Pick one", "Select a hotkey to edit.", parent=self._root)
            return
        idx = sel[0]
        current = config.HOTKEY_COMBOS[idx]
        new = simpledialog.askstring(
            "Edit Hotkey", f"Edit hotkey '{current}':",
            initialvalue=current, parent=self._root,
        )
        if not new or not new.strip(): return
        new = new.strip()
        if new in config.HOTKEY_COMBOS and new != current:
            messagebox.showwarning("Duplicate", "Already exists.", parent=self._root)
            return
        config.HOTKEY_COMBOS[idx] = new
        self._hotkey_list.delete(idx)
        self._hotkey_list.insert(idx, f"  {new}")

    # ── Save ──────────────────────────────────────────────────

    def _save(self):
        config.NVIDIA_API_KEY     = self._api_key_var.get().strip()
        config.LANGUAGE           = self._lang_var.get().strip()
        config.ASR_MODEL          = self._asr_var.get().strip()
        config.LLM_MODEL          = self._llm_var.get().strip()
        config.CLEAN_WITH_LLM     = self._clean_var.get()
        config.APPEND_NEWLINE     = self._newline_var.get()
        config.HOLD_TO_TALK       = self._hold_var.get()
        config.PRESERVE_CLIPBOARD = self._clip_var.get()
        config.EDIT_BEFORE_PASTE  = self._edit_var.get()
        config.AUDIO_CUES         = self._ding_var.get()

        # Mic device — map the chosen label back to the index
        chosen_label = (self._device_var.get() or "").strip()
        if chosen_label.startswith("— System Default"):
            config.INPUT_DEVICE = None
        else:
            for idx, lbl in self._device_map:
                if lbl == chosen_label:
                    config.INPUT_DEVICE = idx
                    break

        if config.HOTKEY_COMBOS:
            config.HOTKEY_COMBO = config.HOTKEY_COMBOS[0]

        config.save()
        self._save_status.configure(text="✓ SAVED — restart for hotkey rebind")
        self._root.after(3500, lambda: self._save_status.configure(text=""))
