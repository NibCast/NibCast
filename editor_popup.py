# ============================================================
#  NibCast — Edit-before-paste popup
# ============================================================
#  Tiny modal that shows the cleaned transcript and lets the user
#  tweak it before injection.
#    Enter   → accept (returns the edited text)
#    Esc     → cancel (returns None)
#    Cmd/Ctrl+Enter → also accepts
#  Run synchronously on its own thread root so it never collides
#  with the FloatingWidget/Settings Tk roots.
# ============================================================

import threading
import tkinter as tk

BG     = "#080808"
PANEL  = "#0d0d0d"
LIME   = "#d4ff3b"
RED    = "#ff2525"
TEXT   = "#f0f0f0"
DIM    = "#5a5a5a"
BORDER = "#2e2e2e"


def edit_text_blocking(initial: str, target_label: str = "", timeout: float = 30.0):
    """Show the edit popup. Returns the edited string, or None if cancelled.

    Blocks the calling thread until the user dismisses the popup or
    `timeout` seconds elapse (in which case the original text is returned).
    """
    result = {"value": initial, "decided": False}
    done = threading.Event()

    def _run():
        try:
            root = tk.Tk()
        except Exception:
            done.set(); return

        root.title("NibCast — Edit before paste")
        root.configure(bg=BG)
        root.overrideredirect(False)
        root.attributes("-topmost", True)
        root.resizable(False, False)

        W, H = 560, 200
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{(sw-W)//2}+{int(sh*0.7)}")

        # Top lime stripe
        tk.Frame(root, bg=LIME, height=2).pack(fill="x")

        # Header
        hdr = tk.Frame(root, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(hdr, text="// EDIT BEFORE PASTE", bg=BG, fg=LIME,
                 font=("Consolas", 9, "bold")).pack(side="left")
        if target_label:
            tk.Label(hdr, text=f"→ {target_label}", bg=BG, fg=DIM,
                     font=("Consolas", 9)).pack(side="right")

        # Text area
        wrap = tk.Frame(root, bg=BORDER)
        wrap.pack(fill="x", padx=14, pady=(0, 8))
        txt = tk.Text(wrap, height=4, bg=BG, fg=TEXT, insertbackground=LIME,
                      font=("Consolas", 11), relief="flat", bd=0,
                      highlightthickness=0, wrap="word", padx=10, pady=8)
        txt.pack(fill="x", padx=1, pady=1)
        txt.insert("1.0", initial)
        txt.focus_set()
        txt.tag_add("sel", "1.0", "end-1c")

        # Buttons
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(0, 12))

        def accept(_=None):
            result["value"]   = txt.get("1.0", "end-1c")
            result["decided"] = True
            try: root.destroy()
            except Exception: pass
            done.set()

        def cancel(_=None):
            result["value"]   = None
            result["decided"] = True
            try: root.destroy()
            except Exception: pass
            done.set()

        def _btn(text, cmd, accent):
            b = tk.Button(bar, text=text, command=cmd,
                          bg=BG, fg=accent, activebackground=accent, activeforeground=BG,
                          font=("Consolas", 9, "bold"), relief="flat", bd=0,
                          padx=14, pady=6, cursor="hand2",
                          highlightthickness=1, highlightbackground=accent)
            b.bind("<Enter>", lambda _e: b.configure(bg=accent, fg=BG))
            b.bind("<Leave>", lambda _e: b.configure(bg=BG, fg=accent))
            return b

        _btn("[ PASTE ⏎ ]", accept, LIME).pack(side="left")
        _btn("[ CANCEL  ESC ]", cancel, RED ).pack(side="left", padx=8)
        tk.Label(bar, text="Enter or Ctrl+Enter to paste · Esc to cancel",
                 bg=BG, fg=DIM, font=("Consolas", 8)).pack(side="right")

        # Key bindings (Enter in a Text widget normally inserts a newline,
        # so bind to Return AND Ctrl+Return; allow Shift+Return for newline)
        def _on_return(e):
            if e.state & 0x0001:        # Shift held → insert newline
                return
            accept()
            return "break"
        txt.bind("<Return>", _on_return)
        txt.bind("<Control-Return>", lambda e: (accept(), "break")[1])
        root.bind("<Escape>", cancel)
        root.protocol("WM_DELETE_WINDOW", cancel)

        # Timeout fallback — accept the *current* text (incl. user edits).
        # Cancellation should be explicit (Esc / Cancel button), not silent.
        def _timeout_accept():
            if not result["decided"]:
                accept()
        root.after(int(timeout * 1000), _timeout_accept)

        root.mainloop()

    threading.Thread(target=_run, daemon=True, name="EditorPopup").start()
    done.wait(timeout=timeout + 1)
    return result["value"]
