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
        "llm_hint": "This may be code. Preserve technical terms.",
    },
    "browser": {
        "label": "🌐 Browser",
        "color": "#fab387",
        "keywords": ["chrome", "firefox", "safari", "edge", "brave", "opera"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Normal punctuation and formatting.",
    },
    "chat": {
        "label": "💬 Chat",
        "color": "#cba6f7",
        "keywords": ["slack", "discord", "teams", "telegram", "whatsapp", "signal"],
        "add_period": False,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Conversational message, no trailing period.",
    },
    "email": {
        "label": "📧 Email",
        "color": "#f38ba8",
        "keywords": ["mail", "outlook", "thunderbird", "gmail"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Professional email tone.",
    },
    "notes": {
        "label": "📝 Notes",
        "color": "#f9e2af",
        "keywords": ["notion", "obsidian", "bear", "evernote", "word", "google docs"],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Clear, structured sentences.",
    },
    "generic": {
        "label": "📄 Generic",
        "color": "#cdd6f4",
        "keywords": [],
        "add_period": True,
        "capitalize": True,
        "remove_fillers": True,
        "llm_hint": "Clean up the text naturally.",
    },
}

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
    try:
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd   = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf    = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.lower()
        elif sys.platform == "darwin":
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first '
                 'application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip().lower()
        else:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip().lower()
    except Exception as e:
        log.warning(f"Window detection failed: {e}")
        return ""


def detect_target() -> dict:
    global _last_detected

    if _manual_override and _manual_override in TARGET_RULES:
        rules = TARGET_RULES[_manual_override].copy()
        rules["category"] = _manual_override
        rules["detected_window"] = "Manual override"
        _last_detected = _manual_override
        return rules

    window = _active_window_name()

    for category, rules in TARGET_RULES.items():
        if category == "generic":
            continue
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
            rules_out = rules.copy()
            rules_out["category"] = category
            rules_out["detected_window"] = window[:60]
            _last_detected = category
            log.info(f"🎯 Auto-detected: {category} ({kw})")
            return rules_out

    fallback = TARGET_RULES["generic"].copy()
    fallback["category"] = "generic"
    fallback["detected_window"] = window[:60] or "unknown"
    _last_detected = "generic"
    return fallback


def get_last_detected() -> str:
    return _last_detected


def all_targets() -> dict:
    return {k: {**v, "category": k} for k, v in TARGET_RULES.items()}


def update_rule(category: str, field: str, value):
    if category in TARGET_RULES and field in TARGET_RULES[category]:
        TARGET_RULES[category][field] = value
        log.info(f"🔧 Rule updated: {category}.{field} = {value}")