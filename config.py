# ============================================================
#  NibCast — Configuration (persisted)
# ============================================================
#  Layered config:
#     defaults (this file)
#       <  persisted JSON   (~/.nibcast/config.json)
#         <  env vars   (NIBCAST_NVIDIA_API_KEY, …)
#  Mutations from Settings/Dashboard call config.save() and are
#  persisted to the JSON sidecar.  Secrets never live in the repo.
# ============================================================

import os
import json
import threading

USER_DIR    = os.path.join(os.path.expanduser("~"), ".nibcast")
CONFIG_FILE = os.path.join(USER_DIR, "config.json")
os.makedirs(USER_DIR, exist_ok=True)

# One-time silent migration from legacy ~/.voiceflow_local directory
_LEGACY_DIR = os.path.join(os.path.expanduser("~"), ".voiceflow_local")
if os.path.exists(_LEGACY_DIR) and not os.path.exists(os.path.join(USER_DIR, "config.json")):
    try:
        import shutil as _shutil
        for _f in os.listdir(_LEGACY_DIR):
            _src = os.path.join(_LEGACY_DIR, _f)
            _dst = os.path.join(USER_DIR, _f)
            if not os.path.exists(_dst):
                _shutil.copy2(_src, _dst) if os.path.isfile(_src) else None
    except Exception:
        pass

# ──────────────────────────────────────────────────────────
# DEFAULTS — these are the lowest-priority source of truth.
# Do NOT put real API keys here.
# ──────────────────────────────────────────────────────────
NVIDIA_API_KEY  = ""   # filled from env / config.json
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_ASR_URL  = "https://integrate.api.nvidia.com/v1/audio/transcriptions"

ASR_MODEL = "nvidia/parakeet-ctc-1.1b-asr"   # unused: NVIDIA has no ASR endpoint (see note below)
# Default NVIDIA LLM. The previous default (llama-3.1-8b) is too small for
# transcript cleanup — it frequently answers the transcript as a chatbot, which
# trips the assistant-response guard in text_processor.py and falls back to
# basic cleanup, giving noticeably worse output than Groq's 70B. Use the larger
# 70B model so NVIDIA-backed cleanup matches the quality users expect.
LLM_MODEL = "meta/llama-3.3-70b-instruct"

# NOTE: NVIDIA's integrate.api.nvidia.com does NOT host audio/ASR endpoints.
# Their /v1/audio/transcriptions returns 404 — use Groq or OpenAI for ASR.

# ASR Backend: "groq" | "openai" | "nvidia" | "local" | "custom"
ASR_BACKEND = "groq"
# LLM Backend: "groq" | "cerebras" | "gemini" | "nvidia" | "openai" | "ollama" | "anthropic" | "custom"
LLM_BACKEND = "groq"
# Optional secondary LLM used ONLY when the primary fails or is rate-limited
# (e.g. Groq's free daily token cap → HTTP 429). Empty = no failover. Opt-in so
# the app never silently sends your text to a provider you didn't choose.
LLM_FALLBACK_BACKEND = ""

# Groq — FREE 7,200 min/day ASR, fastest Whisper, OpenAI-compatible
GROQ_API_KEY   = ""
GROQ_ASR_URL   = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_ASR_MODEL = "whisper-large-v3-turbo"  # fast, used for command dictation
# Phase-1 (wake-word) model. The wake clip is only ~0.6-1 s long, where turbo
# mishears far more often ("hey jarvis" → "Hesige"). The larger whisper-large-v3
# is markedly more accurate on these short clips for ~200 ms more latency — a
# trade worth making, since a missed wake word is a far worse experience than a
# fractional delay. (Command dictation still uses the fast turbo model above.)
GROQ_ASR_MODEL_WAKE = "whisper-large-v3"
GROQ_LLM_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"

# OpenAI (ASR + LLM)
OPENAI_API_KEY   = ""
OPENAI_ASR_URL   = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_ASR_MODEL = "whisper-1"
OPENAI_LLM_URL   = "https://api.openai.com/v1/chat/completions"
OPENAI_LLM_MODEL = "gpt-4o-mini"

# Brain Mode: run 2 ASR backends in parallel, pick better transcript
BRAIN_MODE          = False
# Second ASR engine used ONLY when BRAIN_MODE is on (opt-in). Single-engine Groq
# (free tier) is the default and costs nothing; Brain Mode runs a second engine in
# parallel and keeps the better transcript. Deepgram is the recommended second
# engine (free credits, strong on natural speech). If BRAIN_MODE is on but this
# engine has no key, NibCast quietly uses the primary only.
ASR_BRAIN_SECONDARY = "deepgram"
LLM_BRAIN_SECONDARY = ""

# Anthropic (LLM only)
ANTHROPIC_API_KEY   = ""
ANTHROPIC_LLM_URL   = "https://api.anthropic.com/v1/messages"
ANTHROPIC_LLM_MODEL = "claude-3-5-haiku-20241022"

# Cerebras (LLM only) — FREE, OpenAI-compatible, fastest free throughput.
# Free tier: Llama 3.3 70B / Qwen3, 30 req/min, 1M tokens/day.
# Best Groq alternative for transcript cleanup. Get a key at cloud.cerebras.ai.
# Privacy: free tier is funded by training on inputs — don't send sensitive
# dictation through it if that matters to you.
CEREBRAS_API_KEY   = ""
CEREBRAS_LLM_URL   = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_LLM_MODEL = "llama-3.3-70b"

# Google Gemini (LLM only here) — FREE Flash tier via OpenAI-compatible endpoint.
# Free tier: ~1,500 req/day, 1M-token context. Get a key at aistudio.google.com.
# ⚠ PRIVACY: Google's FREE tier uses your prompts (your dictated text) to improve
# its products. For private dictation use a paid Gemini API key or a different
# backend. Pro models were removed from the free tier in April 2026 — Flash only.
GEMINI_API_KEY   = ""
GEMINI_LLM_URL   = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_LLM_MODEL = "gemini-2.5-flash"

# Ollama / local (LLM only via OpenAI-compatible endpoint)
OLLAMA_BASE_URL  = "http://localhost:11434/v1"
OLLAMA_LLM_MODEL = "llama3.2"

# Local ASR server (OpenAI-compatible, e.g. whisper.cpp server)
LOCAL_ASR_URL   = "http://localhost:8000/v1/audio/transcriptions"
LOCAL_ASR_MODEL = "whisper-1"

# Deepgram (ASR only) — nova-3 is their most accurate model for general speech
# Free tier: 12,000 min/month. Get a key at console.deepgram.com
DEEPGRAM_API_KEY   = ""
DEEPGRAM_ASR_URL   = "https://api.deepgram.com/v1/listen"
DEEPGRAM_ASR_MODEL = "nova-3"   # nova-3 (best), nova-2, base, enhanced
DEEPGRAM_DIARIZE   = False      # True → label each speaker ("Speaker 1: …\nSpeaker 2: …")

# Custom API (ASR + LLM, any OpenAI-compatible endpoint)
CUSTOM_API_KEY   = ""
CUSTOM_ASR_URL   = ""
CUSTOM_ASR_MODEL = ""
CUSTOM_LLM_URL   = ""
CUSTOM_LLM_MODEL = ""

# Wake Word
WAKE_WORD         = ""
WAKE_WORD_ENABLED = False
# Known Whisper mis-transcriptions of your wake phrase.
# If you observe a consistent mishear (e.g. "hey voice" → "the flow"),
# add it here and the system will accept it as a valid wake trigger.
# Example: WAKE_WORD_ALTERNATIVES = ["the flow", "hey boys", "hey noise"]
WAKE_WORD_ALTERNATIVES: list = []
# How long (seconds) the "command window" stays open after wake word is confirmed.
WAKE_WORD_LISTEN_SEC = 12.0
# Minimum phonetic similarity (Jaro, 0–1) for the loose "first word exact + second
# word similar" fuzzy wake match. Higher = stricter (fewer false wakes from
# background media); lower = more forgiving of Whisper mishears. 0.80 blocks common
# near-misses like "Hey Fowler" ≈ "hey flow" (Jaro 0.76) while still catching close
# same-speaker mishears (e.g. "hey glow"/"hey low").
WAKE_WORD_FUZZY_THRESHOLD = 0.80
# Voice enrollment (speaker verification for wake phrase).
# Default OFF: the lightweight spectral matcher rejected genuine same-speaker
# clips often enough (scores ~0.50) to make the wake word feel broken, so out of
# the box the wake word should "just work" for the person using the machine.
# It's opt-in for users who specifically want to lock the wake word to one voice
# (e.g. a shared room); when enabled, the threshold is forgiving so the real user
# isn't turned away. Raise VOICE_SIMILARITY_THRESHOLD if others get falsely accepted.
VOICE_ENROLLMENT_ENABLED   = False   # when True, reject wake/command clips that don't match
                                     # the enrolled voice (no-op until a voice is enrolled).
VOICE_SIMILARITY_THRESHOLD = 0.50    # 0–1; lower = more permissive; raise if false accepts

# Phase 1 (sleep mode) VAD settings — tighter than general VAD to avoid sending
# every ambient sentence to the cloud ASR.
# Raise WAKE_WORD_VAD_THRESHOLD if the mic picks up background conversation;
# lower it if "hey voice" is not being detected consistently.
# 0.05 was marginal: real-world mic gains put a normal speaking voice right at
# ~0.05 RMS (see enrollment levels), so the phrase only flickered across the gate
# and got chopped into "clip too short" fragments — i.e. the wake word "never
# caught" on quieter mics. 0.03 reliably clears a spoken phrase while still
# sitting above room tone. Auto-raised back up if background audio keeps
# triggering (see main.py Wake L1b).
WAKE_WORD_VAD_THRESHOLD  = 0.03   # tune down if wake phrase is missed; up if background noise triggers it
WAKE_WORD_SILENCE_SEC    = 0.70   # 0.55 chopped phrases with a mid-word pause ("hey…jarvis"); 0.70 holds the whole phrase
WAKE_WORD_TRIGGER_SEC    = 0.15   # reduced from 0.25 → recording fires sooner on voice onset
WAKE_WORD_MAX_RECORD_SEC = 3.0    # room for the full phrase + trailing silence at the larger silence window

# Whisper transcription hint — passed as initial_prompt to guide the model.
# Include domain-specific vocabulary so Whisper recognises technical terms correctly.
# Example: "wake word, hotkey, NibCast, dictation" prevents mishears like
# "wake word" → "vehicle patient" or "hotkey" → "hot key".
WHISPER_PROMPT = "NibCast, wake word, hotkey, dictation, transcription, Alt+Space, dashboard, toggle, recording, clipboard"

# Audio
SAMPLE_RATE  = 16000
CHANNELS     = 1
AUDIO_FORMAT = "int16"
INPUT_DEVICE = None        # None => system default; int => sounddevice index

# Hotkeys
HOTKEY_COMBOS = [
    "<ctrl>+<alt>+v",             # confirmed working
    "<ctrl>+<alt>+<space>",       # reliable: Ctrl+Alt+Space not used by Windows/browsers
    "<ctrl>+<shift>+<space>",     # command mode: select text, hold to speak an editing instruction
    "<scroll_lock>",              # almost never claimed by other apps
]
HOTKEY_COMBO = HOTKEY_COMBOS[0]   # back-compat alias
HOLD_TO_TALK = True               # back-compat alias

# Undo last dictation — press this to Ctrl+Z the most recent paste.
# Set to "" to disable.  Default: Ctrl+Alt+Z (a "scratch that" undo).
UNDO_HOTKEY = "<ctrl>+<alt>+z"

# Per-hotkey recording modes.  Each entry: {"combo": "...", "mode": "hold"|"toggle"|"command"}
#   hold    → hold the key while speaking, release to paste
#   toggle  → press once to start, press again (or widget click) to stop and paste
#   command → select text in any app, hold to speak an editing instruction; LLM rewrites selection
HOTKEY_CONFIGS = [
    {"combo": "<ctrl>+<alt>+v",           "mode": "hold"},
    {"combo": "<ctrl>+<alt>+<space>",     "mode": "toggle"},
    {"combo": "<ctrl>+<shift>+<space>",   "mode": "command"},
    {"combo": "<scroll_lock>",            "mode": "hold"},
]

# Recording mode: "hold" | "toggle" | "voice"  (kept for backward compat and voice-mode flag)
RECORDING_MODE = "hold"

# When True, Whisper translates non-English speech directly into English text
# instead of transcribing it in the original language.
TRANSLATE_TO_ENGLISH = False

# Wake-word pause list: window title substrings that suppress Phase-1 VAD.
# When any of these strings appears in the active window title, NibCast skips
# wake-word detection so it doesn't compete with another voice app on the mic.
# Example: ["wispr", "flow", "dragon"]
VAD_PAUSE_APPS: list = ["wispr flow", "wispr", "dragon"]

# Behaviour
CLEAN_WITH_LLM     = True
LANGUAGE           = "en"    # "" => let ASR auto-detect
APPEND_NEWLINE     = False
SHOW_NOTIFICATION  = True
PRESERVE_CLIPBOARD = True    # restore user's clipboard after paste
EDIT_BEFORE_PASTE  = False   # pop a tiny editor before injecting
AUDIO_CUES         = True    # ding on start/stop

# Writing style — controls how aggressively the LLM cleans the transcript.
#   "flow"         Flowing prose: smart formatting, light restructuring (default)
#   "verbatim"     Minimal touch: only filler words + punctuation, never restructure
#   "professional" Formal, polished prose
#   "concise"      Strip to the essential point, ~40% shorter
WRITING_STYLE = "flow"

# Snippets: map spoken phrases → expanded text.
# Keys are lowercase spoken phrases; values are the text to paste.
# Example: {"my email": "user@example.com", "sign off": "Best regards,\nYour Name"}
SNIPPETS: dict = {}

# Network
HTTP_TIMEOUT  = 30
HTTP_RETRIES  = 3            # transient-error retries with exp backoff

# Activation
ACTIVATION_MODE       = "hotkey"  # "hotkey", "click", "voice", "both"
VOICE_VAD_THRESHOLD   = 0.030   # raised from 0.015 to reduce ambient-noise false-triggers
VOICE_VAD_SILENCE_SEC = 2.0

# UI
WIDGET_STYLE = "wave"   # "wave", "orbit", "pulse"  — icon drawn inside the widget
WIDGET_SHAPE = "orb"    # "orb", "bar", "chip"       — overall widget geometry
WIDGET_THEME = "amber"  # "amber", "violet", "cyan" — idle colour palette

# Startup / background behaviour
START_MINIMIZED     = False   # launch without showing the floating widget or dashboard
RUN_AT_STARTUP      = False   # Windows registry auto-start (managed via /api/autostart)
SHOW_WIDGET_ON_START = True   # show the floating orb when not in minimized mode

# Audio cue fine-grain controls
AUDIO_CUE_START     = True    # ding when recording begins
AUDIO_CUE_STOP      = True    # ding when recording ends
AUDIO_CUE_ERROR     = True    # error sound

# Data & Privacy
PRIVACY_MODE        = False   # suppress all transcript text from logs and history
CONTEXT_AWARENESS   = True    # detect active app and use per-app vocabulary hints
HISTORY_AUTO_DELETE_DAYS = 0  # 0 = never auto-delete; N = delete entries older than N days

# ── Keys that are safe to persist to disk ────────────────────
_PERSISTED_KEYS = (
    "NVIDIA_API_KEY", "ASR_MODEL", "LLM_MODEL",
    "SAMPLE_RATE", "INPUT_DEVICE",
    "HOTKEY_COMBOS", "HOTKEY_CONFIGS", "HOLD_TO_TALK",
    "CLEAN_WITH_LLM", "LANGUAGE", "APPEND_NEWLINE",
    "SHOW_NOTIFICATION", "PRESERVE_CLIPBOARD",
    "EDIT_BEFORE_PASTE", "AUDIO_CUES",
    "HTTP_TIMEOUT", "HTTP_RETRIES",
    "ACTIVATION_MODE", "VOICE_VAD_THRESHOLD", "VOICE_VAD_SILENCE_SEC",
    "WIDGET_STYLE", "WIDGET_SHAPE", "WIDGET_THEME",
    "TRANSLATE_TO_ENGLISH",
    "VAD_PAUSE_APPS",
    "VOICE_ENROLLMENT_ENABLED", "VOICE_SIMILARITY_THRESHOLD",
    "START_MINIMIZED", "RUN_AT_STARTUP", "SHOW_WIDGET_ON_START",
    "AUDIO_CUE_START", "AUDIO_CUE_STOP", "AUDIO_CUE_ERROR",
    "PRIVACY_MODE", "CONTEXT_AWARENESS", "HISTORY_AUTO_DELETE_DAYS",
    # Backends
    "ASR_BACKEND", "LLM_BACKEND", "LLM_FALLBACK_BACKEND",
    "GROQ_API_KEY", "GROQ_ASR_MODEL", "GROQ_LLM_MODEL",
    "OPENAI_API_KEY", "OPENAI_ASR_MODEL", "OPENAI_LLM_MODEL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_LLM_MODEL",
    "CEREBRAS_API_KEY", "CEREBRAS_LLM_MODEL",
    "GEMINI_API_KEY", "GEMINI_LLM_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_LLM_MODEL",
    "LOCAL_ASR_URL", "LOCAL_ASR_MODEL",
    "DEEPGRAM_API_KEY", "DEEPGRAM_ASR_URL", "DEEPGRAM_ASR_MODEL", "DEEPGRAM_DIARIZE",
    "CUSTOM_API_KEY", "CUSTOM_ASR_URL", "CUSTOM_ASR_MODEL",
    "CUSTOM_LLM_URL", "CUSTOM_LLM_MODEL",
    # Brain Mode
    "BRAIN_MODE", "ASR_BRAIN_SECONDARY", "LLM_BRAIN_SECONDARY",
    # Wake word
    "WAKE_WORD", "WAKE_WORD_ENABLED", "WAKE_WORD_LISTEN_SEC",
    "WAKE_WORD_FUZZY_THRESHOLD",
    "WAKE_WORD_VAD_THRESHOLD", "WAKE_WORD_SILENCE_SEC",
    "WAKE_WORD_TRIGGER_SEC", "WAKE_WORD_MAX_RECORD_SEC",
    "WAKE_WORD_ALTERNATIVES",
    # Groq wake model
    "GROQ_ASR_MODEL_WAKE",
    # Recording mode
    "RECORDING_MODE",
    # Whisper transcription hint
    "WHISPER_PROMPT",
    # Snippets
    "SNIPPETS",
    # Writing style
    "WRITING_STYLE",
)

_lock = threading.Lock()


def _coerce_loaded(g, key, value):
    """Light type-coerce so config.json values don't poison runtime types."""
    cur = g.get(key)
    if isinstance(cur, bool):    return bool(value)
    if isinstance(cur, int) and not isinstance(cur, bool):
        try: return int(value)
        except (TypeError, ValueError): return cur
    if isinstance(cur, float):
        try: return float(value)
        except (TypeError, ValueError): return cur
    if isinstance(cur, list):
        if isinstance(value, list): return value
        if isinstance(value, str):  return [s.strip() for s in value.splitlines() if s.strip()]
        return cur
    return value


def load():
    """Load persisted config from JSON, then overlay env vars."""
    g = globals()

    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            # utf-8-sig strips BOM if present (written by PowerShell/Notepad)
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f) or {}
            for k, v in data.items():
                if k in _PERSISTED_KEYS:
                    g[k] = _coerce_loaded(g, k, v)
        except Exception:
            # corrupted config is non-fatal — fall through to defaults
            data = {}

    # Normalise hotkey combo formats saved by older versions.
    # Angle brackets around letter keys (<v>) are invalid pynput syntax;
    # only special key names (ctrl, shift, space, f9 …) use angle brackets.
    _SPECIAL_HK = frozenset({
        "ctrl", "shift", "alt", "space", "enter", "return", "tab",
        "backspace", "delete", "escape", "esc",
        "up", "down", "left", "right", "home", "end",
        "page_up", "page_down", "insert", "caps_lock",
        "num_lock", "scroll_lock", "print_screen", "pause",
        *(f"f{i}" for i in range(1, 25)),
    })

    def _fix_combo(c):
        parts = []
        for tok in c.split("+"):
            tok = tok.strip()
            if tok.startswith("<") and tok.endswith(">"):
                inner = tok[1:-1].lower()
                parts.append(f"<{inner}>" if inner in _SPECIAL_HK else inner)
            else:
                parts.append(tok)
        return "+".join(parts)

    if isinstance(g.get("HOTKEY_COMBOS"), list):
        g["HOTKEY_COMBOS"] = [_fix_combo(c) for c in g["HOTKEY_COMBOS"]]
        g["HOTKEY_COMBO"]  = g["HOTKEY_COMBOS"][0] if g["HOTKEY_COMBOS"] else ""

    # Normalise combo strings inside HOTKEY_CONFIGS, then deduplicate.
    # Keep the LAST occurrence so that if the same combo appears twice the
    # most recently appended entry (with its mode) wins.
    if isinstance(g.get("HOTKEY_CONFIGS"), list):
        for hc in g["HOTKEY_CONFIGS"]:
            if isinstance(hc, dict) and hc.get("combo"):
                hc["combo"] = _fix_combo(hc["combo"])
        seen = {}
        for hc in g["HOTKEY_CONFIGS"]:
            if isinstance(hc, dict) and hc.get("combo"):
                seen[hc["combo"]] = hc
        g["HOTKEY_CONFIGS"] = list(seen.values())

    # Build / migrate HOTKEY_CONFIGS
    if data and "HOTKEY_CONFIGS" not in data:
        # Existing config file without HOTKEY_CONFIGS — derive from HOTKEY_COMBOS + RECORDING_MODE
        rm = g.get("RECORDING_MODE", "hold")
        hk_mode = "toggle" if rm == "toggle" else "hold"
        g["HOTKEY_CONFIGS"] = [{"combo": c, "mode": hk_mode}
                               for c in g.get("HOTKEY_COMBOS", [])]
    elif "HOTKEY_CONFIGS" in data:
        # Keep HOTKEY_COMBOS derived from HOTKEY_CONFIGS
        g["HOTKEY_COMBOS"] = [hc["combo"] for hc in g["HOTKEY_CONFIGS"]
                              if isinstance(hc, dict) and hc.get("combo")]
        if g["HOTKEY_COMBOS"]:
            g["HOTKEY_COMBO"] = g["HOTKEY_COMBOS"][0]

    # Migrate: derive RECORDING_MODE from legacy ACTIVATION_MODE + HOLD_TO_TALK
    # when RECORDING_MODE is not already present in the persisted config.
    if "RECORDING_MODE" not in data:
        act = g.get("ACTIVATION_MODE", "hotkey")
        htt = g.get("HOLD_TO_TALK", True)
        if act == "voice":
            g["RECORDING_MODE"] = "voice"
        elif htt:
            g["RECORDING_MODE"] = "hold"
        else:
            g["RECORDING_MODE"] = "toggle"

    # Env vars (highest priority for secrets)
    env_key = os.environ.get("NIBCAST_NVIDIA_API_KEY", "").strip()
    if env_key:
        g["NVIDIA_API_KEY"] = env_key

    if g.get("HOTKEY_COMBOS"):
        g["HOTKEY_COMBO"] = g["HOTKEY_COMBOS"][0]


def save():
    """Atomically write current values to disk.  Never raises — but logs
    a warning on failure so the user knows their change wasn't persisted."""
    with _lock:
        g = globals()
        data = {k: g[k] for k in _PERSISTED_KEYS if k in g}
        tmp = CONFIG_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
        except Exception as e:
            # Lazy-import the logger so config.py stays import-cycle safe.
            try:
                from logger import log
                log.warning(f"⚠️  config.save() failed: {e} — changes are in-memory only")
            except Exception:
                pass
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except Exception:
                pass


def reset_defaults():
    """Remove the persisted file. Defaults take effect on next start."""
    try:
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
    except Exception:
        pass


# Load on import.
load()
