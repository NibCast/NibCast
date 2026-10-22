# ============================================================
#  NibCast — Target App Detection
# ============================================================
import re
import sys
import subprocess
from logger import log

TARGET_RULES = {
    "terminal": {
        "label": "🖥️ Terminal",
        "color": "#a6e3a1",
        "keywords": ["terminal", "iterm", "cmd.exe", "powershell", "windows terminal",
                     "gnome-terminal", "konsole", "bash", "zsh"],
        "add_period": False,
        "capitalize": False,
        "remove_fillers": True,
        "llm_hint": "This is a terminal command. Keep it as a shell command.",
    },
    "vscode": {
        "label": "💻 VS Code",
        "color": "#89b4fa",
        "keywords": ["code", "visual studio code", "cursor", "vim", "neovim",
                     "sublime text", "notepad++"],
        "add_period": False,
        "capitalize": False,
        "remove_fillers": False,
        "llm_hint": "This may be code or Markdown. Preserve technical terms.",
        "markdown": True,
    },
    # AI chat apps — checked before "browser" so ChatGPT/Claude in a browser tab
    # (window title "ChatGPT - Google Chrome") still counts as a prompt.
    "ai": {
        "label": "🤖 AI Prompt",
        "color": "#94e2d5",
        "keywords": ["chatgpt", "claude", "gemini", "copilot", "perplexity", "deepseek",
                     "le chat"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": ("This is a prompt the speaker is writing TO an AI assistant. Keep it "
                     "as their request in their own voice — never answer it. Keep every "
                     "detail, constraint and example they gave; put multi-part requests "
                     "as numbered points."),
        "markdown": True,
    },
    "browser": {
        "label": "🌐 Browser",
        "color": "#fab387",
        "keywords": ["chrome", "firefox", "safari", "edge", "brave", "opera"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Normal punctuation and formatting.",
        "markdown": False,
    },
    "chat": {
        "label": "💬 Chat",
        "color": "#cba6f7",
        "keywords": ["slack", "discord", "teams", "telegram", "whatsapp", "signal"],
        "add_period": False,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": ("Chat message: short, casual and direct, no trailing period. Keep names "
                     "and @mentions exactly as said."),
        "markdown": False,
    },
    "email": {
        "label": "📧 Email",
        "color": "#f38ba8",
        "keywords": ["mail", "outlook", "thunderbird", "gmail"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": ("This is an email. Put a greeting the speaker said (e.g. \"Hi Sam,\") on "
                     "its own line, the body in short paragraphs, and a sign-off they said "
                     "(e.g. \"Thanks,\" + name) on its own lines. Never invent a greeting or "
                     "sign-off. Professional, friendly tone."),
        "markdown": False,
    },
    "notes": {
        "label": "📝 Notes",
        "color": "#f9e2af",
        "keywords": ["notion", "obsidian", "bear", "evernote", "word", "google docs"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Notes: clear, structured sentences; lists and tables welcome.",
        "markdown": False,
        "markdown_keywords": ["notion", "obsidian", "bear"],   # Word/Docs/Evernote paste pipes as text
    },
    "generic": {
        "label": "📄 Generic",
        "color": "#cdd6f4",
        "keywords": [],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Clean up the text naturally.",
        "markdown": False,
    },
}

# Detection order: specific apps first, then AI chat, then the generic browser.
# Email/chat/notes win over "ai" so "Gmail - claude.smith@… - Chrome" or
# "RE: Gemini launch - Outlook" stay emails; "ai" wins over "browser" so a
# ChatGPT tab is still a prompt.
_MATCH_ORDER = ("terminal", "vscode", "chat", "email", "notes", "ai", "browser")

_MD_YES = " Markdown is supported here — use a Markdown table for tabular details."
_MD_NO  = (" Plain text only — no Markdown tables, headings or **bold**; use numbered "
           "lists (\"1. Item — detail\") for tabular details.")


def _finish(rules: dict, category: str, window: str, keyword: str = "") -> dict:
    """Copy a rule set for one detection and append the Markdown capability to
    the LLM hint, so tables are only produced where they will render."""
    out = rules.copy()
    md = out.get("markdown", False) or keyword in out.get("markdown_keywords", ())
    out["markdown"] = md
    out["llm_hint"] = (out.get("llm_hint", "") + (_MD_YES if md else _MD_NO)).strip()
    out["category"] = category
    out["detected_window"] = window
    return out

_manual_override = ""
_last_detected   = "generic"


def set_override(category: str):
    global _manual_override
    if category and category not in TARGET_RULES:
        log.warning(f"🎯 Ignored invalid override category: {category}")
        return
    _manual_override = category
    log.info(f"🎯 Override → {category or 'auto'}")


def clear_override():
    global _manual_override
    _manual_override = ""


def get_override() -> str:
    return _manual_override


def _active_window_name() -> str:
    """Lower-cased foreground window title (for keyword matching)."""
    return active_window_title().lower()


def active_window_title() -> str:
    """Foreground window title with its original capitalisation."""
    try:
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd   = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf    = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        elif sys.platform == "darwin":
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first '
                 'application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip()
        else:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip()
    except Exception as e:
        log.warning(f"Window detection failed: {e}")
        return ""


def detect_target() -> dict:
    global _last_detected

    if _manual_override and _manual_override in TARGET_RULES:
        _last_detected = _manual_override
        return _finish(TARGET_RULES[_manual_override], _manual_override, "Manual override")

    window = _active_window_name()

    for category in _MATCH_ORDER:
        rules = TARGET_RULES[category]
        for kw in rules["keywords"]:
            # Word-boundary match so "code" doesn't match "Discord" /
            # "iCODE" / etc.  Multi-word keywords ("visual studio code")
            # remain substring-matched because \b doesn't apply mid-phrase.
            if " " in kw:
                if kw not in window:
                    continue
            else:
                if not re.search(r"\b" + re.escape(kw) + r"\b", window):
                    continue
            _last_detected = category
            log.info(f"🎯 Auto-detected: {category} ({kw})")
            return _finish(rules, category, window[:60], kw)

    _last_detected = "generic"
    return _finish(TARGET_RULES["generic"], "generic", window[:60] or "unknown")


def get_last_detected() -> str:
    return _last_detected


def all_targets() -> dict:
    return {k: {**v, "category": k} for k, v in TARGET_RULES.items()}


def update_rule(category: str, field: str, value):
    if category in TARGET_RULES and field in TARGET_RULES[category]:
        TARGET_RULES[category][field] = value
        log.info(f"🔧 Rule updated: {category}.{field} = {value}")