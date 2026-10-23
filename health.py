# ============================================================
#  NibCast — System Health
# ============================================================
#  One place that answers "why isn't it working?" in plain
#  words. Each check returns:
#
#    {"id", "area", "status": "ok" | "warn" | "error" | "info",
#     "title", "detail", "fixes": [{"label", "action"}]}
#
#  Fix actions are applied by apply_fix() (dashboard buttons):
#  switching everything to offline or to online, adding a local
#  fallback, loading In-app Whisper, downloading the wake model…
#  Network checks are cached briefly so the panel can refresh
#  often without hammering providers.
# ============================================================

from __future__ import annotations

import threading
import time

import requests

import config
from logger import log, RECENT_PROBLEMS

_CACHE_SEC = 60
_cache: dict = {}
_cache_lock = threading.Lock()

# Plain-language meaning of a history row's llm_status.
_STATUS_TEXT = {
    "rate_limited":   "the cleanup provider is rate-limiting you (too many requests or tokens)",
    "timeout":        "the cleanup provider didn't answer in time",
    "connection":     "the cleanup provider couldn't be reached (offline, VPN or firewall?)",
    "guard":          "the LLM replied like a chatbot instead of cleaning, so its answer was discarded",
    "not_configured": "the cleanup LLM isn't set up (missing API key or model)",
    "offline":        "Offline Mode blocked a cloud LLM",
    "error":          "the cleanup LLM returned an error",
    "empty":          "the cleanup LLM returned nothing",
}


def explain_llm_status(status: str) -> str:
    """'basic:rate_limited' → a sentence the user can act on ('' when fine)."""
    if not status or not status.startswith("basic:"):
        return ""
    reason = status.split(":", 1)[1]
    if reason == "http_404":
        return ("the cleanup model doesn't exist at that provider any more (providers retire "
                "models) — pick a current one in Config → AI Backend")
    if reason.startswith("http_"):
        return f"the cleanup LLM returned HTTP {reason[5:]}"
    return _STATUS_TEXT.get(reason, reason.replace("_", " "))


def _check(id, area, status, title, detail="", fixes=()):
    return {"id": id, "area": area, "status": status, "title": title,
            "detail": detail, "fixes": [{"label": l, "action": a} for l, a in fixes]}


# ── Connectivity (cached) ─────────────────────────────────────

def _probe(key: str, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_SEC:
            return hit[1]
    try:
        result = fn()
    except Exception as e:
        result = ("down", str(e)[:120])
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def _http_status(url, headers):
    """('ok'|'auth'|'down'|'http', detail)"""
    try:
        r = requests.get(url, headers=headers, timeout=4)
    except requests.exceptions.RequestException as e:
        return ("down", type(e).__name__)
    if r.status_code == 200:
        return ("ok", "")
    if r.status_code in (401, 403):
        return ("auth", f"HTTP {r.status_code}")
    if r.status_code == 429:
        return ("ok", "rate-limited right now")
    return ("http", f"HTTP {r.status_code}")


_CLOUD_ASR = {
    "groq":     ("GROQ_API_KEY", "https://api.groq.com/openai/v1/models", "Bearer", "console.groq.com"),
    "openai":   ("OPENAI_API_KEY", "https://api.openai.com/v1/models", "Bearer", "platform.openai.com"),
    "deepgram": ("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects", "Token", "console.deepgram.com"),
}
_CLOUD_LLM_KEY = {
    "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "nvidia": "NVIDIA_API_KEY",
}


def _server_key(url: str):
    """(host, port) with localhost spellings unified, so 'http://localhost:11434',
    'http://127.0.0.1:11434/v1/' and 'http://[::1]:11434/v1' all match."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url if "://" in url else "http://" + url)
        host = "127.0.0.1" if u.hostname in ("localhost", "127.0.0.1", "::1") else u.hostname
        return (host, u.port)
    except ValueError:
        return (url, None)


def _local_llm_state() -> tuple:
    """(state, detail) for the configured local server + model:
    'ok' | 'no_server' | 'no_model' | 'not_set'."""
    import local_models
    base = getattr(config, "OLLAMA_BASE_URL", "") or ""
    model = getattr(config, "OLLAMA_LLM_MODEL", "") or ""
    if not base or not model:
        return ("not_set", "")

    want = _server_key(base)

    def probe():
        servers = local_models.detect(timeout=0.8)
        for s in servers:
            if _server_key(s["base_url"]) == want:
                return ("ok", "") if model in s["models"] else ("no_model", ", ".join(s["models"][:5]))
        return ("no_server", ", ".join(s["name"] for s in servers))
    return _probe(f"local:{base}:{model}", probe)


# ── Individual checks ─────────────────────────────────────────

_MIC_FIX = ("Check Windows Settings → Privacy → Microphone (allow desktop apps), that the right "
            "input device is selected in Config, that the mic isn't muted, and its input level in "
            "Windows Sound settings. Restart NibCast after changing it.")
_MIC_PROBLEMS = ("Failed to open input device", "No audio captured", "MIC TOO QUIET",
                 "Mic is consistently too quiet", "near-silence")


def check_microphone(mic_level_at: float) -> dict:
    """The mic stream is only open continuously while voice activation is on;
    in hotkey-only mode it closes between dictations, so 'no audio lately' is
    normal there — judge by what the last recordings reported instead."""
    recent = [p for p in RECENT_PROBLEMS if time.time() - p["time"] < 600
              and any(k in p["message"] for k in _MIC_PROBLEMS)]
    if recent:
        return _check("mic", "Microphone", "error", "The microphone had problems recently",
                      recent[-1]["message"][:160] + " — " + _MIC_FIX)
    if mic_level_at and time.time() - mic_level_at < 5:
        return _check("mic", "Microphone", "ok", "Microphone is delivering audio")
    if getattr(config, "WAKE_WORD_ENABLED", False):
        return _check("mic", "Microphone", "error", "No audio from the microphone",
                      "Voice activation is on, so the mic should be streaming, but no sound has "
                      "arrived for a few seconds. " + _MIC_FIX)
    return _check("mic", "Microphone", "ok", "Microphone idle (checked each time you dictate)")


def check_asr() -> dict:
    backend = (getattr(config, "ASR_BACKEND", "groq") or "groq").lower()
    offline = getattr(config, "OFFLINE_MODE", False)
    go_offline = ("Switch everything to offline", "use_offline")
    if backend == "faster_whisper":
        import local_asr
        st = local_asr.status()
        if not st["available"]:
            return _check("asr", "Speech-to-text", "error", "In-app Whisper is selected but not installed",
                          "Run  pip install faster-whisper  in NibCast's Python, then restart NibCast. "
                          "Until then nothing can be transcribed.",
                          [("Use Groq (online) instead", "use_online")])
        if st["state"] == "ready":
            return _check("asr", "Speech-to-text", "ok", f"In-app Whisper ready ({st['model']} on {st['device']})")
        if st["state"] == "loading":
            return _check("asr", "Speech-to-text", "warn", "In-app Whisper is still loading",
                          "The first load downloads the model — dictation works once it's ready.")
        if st["state"] == "error":
            return _check("asr", "Speech-to-text", "error", "In-app Whisper failed to load",
                          st.get("error", ""), [("Try loading again", "load_whisper"),
                                                ("Use Groq (online) instead", "use_online")])
        return _check("asr", "Speech-to-text", "warn", "In-app Whisper isn't loaded yet",
                      "It loads on first use, which delays that dictation.", [("Load it now", "load_whisper")])
    if backend in _CLOUD_ASR:
        key_name, url, scheme, site = _CLOUD_ASR[backend]
        if offline:
            return _check("asr", "Speech-to-text", "error",
                          f"Offline Mode is on but speech-to-text is {backend.title()} (cloud)",
                          "Offline Mode blocks cloud services, so nothing can be transcribed.",
                          [("Switch everything to offline", "use_offline"),
                           ("Turn Offline Mode off", "offline_off")])
        key = getattr(config, key_name, "") or ""
        if not key:
            return _check("asr", "Speech-to-text", "error", f"No {backend.title()} API key",
                          f"Add a key in Config → AI Backend (free at {site}), or go fully offline.",
                          [go_offline])
        state, detail = _probe(f"asr:{backend}:{key[-6:]}",
                               lambda: _http_status(url, {"Authorization": f"{scheme} {key}"}))
        if state == "ok":
            return _check("asr", "Speech-to-text", "ok", f"{backend.title()} speech-to-text reachable",
                          detail)
        if state == "auth":
            return _check("asr", "Speech-to-text", "error", f"{backend.title()} rejected your API key",
                          f"{detail}. The key is wrong, expired or revoked — paste a new one in Config → AI Backend.",
                          [go_offline])
        if state == "down":
            return _check("asr", "Speech-to-text", "error", f"Can't reach {backend.title()}",
                          f"No connection ({detail}). Check your internet, VPN or firewall — or work offline.",
                          [go_offline])
        return _check("asr", "Speech-to-text", "warn", f"{backend.title()} answered {detail}",
                      "It may be a temporary provider problem.", [go_offline])
    if backend in ("local", "custom"):
        url = getattr(config, f"{backend.upper()}_ASR_URL", "") or ""
        if not url:
            return _check("asr", "Speech-to-text", "error", f"No URL set for the {backend} speech server",
                          "Set it in Config → AI Backend, or use In-app Whisper.", [go_offline])
        root = url.split("/v1/")[0] if "/v1/" in url else url
        state, detail = _probe(f"asr:{url}", lambda: _http_status(root, {}))
        if state == "down":
            return _check("asr", "Speech-to-text", "error", f"The {backend} speech server isn't running",
                          f"{url} didn't answer ({detail}). Start it, or switch to In-app Whisper.",
                          [go_offline])
        return _check("asr", "Speech-to-text", "ok", f"{backend.title()} speech server is answering")
    return _check("asr", "Speech-to-text", "error", f"Unknown speech backend '{backend}'",
                  "Pick one in Config → AI Backend.", [("Use Groq (online)", "use_online"), go_offline])


def _llm_check(role: str, backend: str) -> dict:
    """Health of one cleanup engine (primary / fallback / hybrid)."""
    cid = f"llm_{role}"
    area = {"primary": "Cleanup LLM", "fallback": "Fallback LLM", "hybrid": "Hybrid cleanup"}[role]
    name = "Local model" if backend == "ollama" else backend.title()
    offline = getattr(config, "OFFLINE_MODE", False)
    if backend == "ollama":
        state, detail = _local_llm_state()
        model = getattr(config, "OLLAMA_LLM_MODEL", "")
        if state == "ok":
            return _check(cid, area, "ok", f"Local model ready: {model}")
        if state == "not_set":
            return _check(cid, area, "error", "No local model chosen",
                          "Click DETECT LOCAL MODELS in Config → AI Backend.", [("Find a local model", "detect_local")])
        if state == "no_model":
            return _check(cid, area, "error", f"The local server doesn't have '{model}'",
                          f"Available: {detail or 'none'}. Pull it (e.g.  ollama pull {model}) or pick another.",
                          [("Use an available local model", "detect_local")])
        return _check(cid, area, "error", "The local model server isn't running",
                      "Start Ollama / LM Studio / llama.cpp, then check again"
                      + (f" (found running: {detail})" if detail else "") + ".",
                      [("Use an available local model", "detect_local")])
    if backend in ("custom",):
        return _check(cid, area, "info", "Custom LLM endpoint", "Not checked automatically — use TEST LLM.")
    if offline:
        return _check(cid, area, "error", f"Offline Mode is on but {area.lower()} is {name} (cloud)",
                      "Offline Mode blocks it, so dictation is pasted without cleanup.",
                      [("Switch everything to offline", "use_offline"), ("Turn Offline Mode off", "offline_off")])
    key_name = _CLOUD_LLM_KEY.get(backend)
    key = (getattr(config, key_name, "") or "") if key_name else ""
    if not key:
        return _check(cid, area, "error", f"No {name} API key",
                      "Add it in Config → AI Backend — without it this engine can't clean your text.",
                      [("Use a local model instead", "detect_local")])
    import text_processor
    p = text_processor._llm_params(backend)
    if backend == "anthropic":
        url, hdrs = "https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif p:
        url, hdrs = p["url"].replace("/chat/completions", "/models"), {"Authorization": f"Bearer {key}"}
    else:
        return _check(cid, area, "error", f"{name} isn't usable", "Check its settings in Config → AI Backend.")
    state, detail = _probe(f"llm:{backend}:{key[-6:]}", lambda: _http_status(url, hdrs))
    if state == "ok":
        return _check(cid, area, "ok", f"{name} reachable", detail)
    if state == "auth":
        return _check(cid, area, "error", f"{name} rejected your API key",
                      f"{detail}. Paste a new key in Config → AI Backend.", [("Use a local model instead", "detect_local")])
    if state == "down":
        return _check(cid, area, "error", f"Can't reach {name}",
                      f"No connection ({detail}). Your text will be pasted without cleanup until it's back.",
                      [("Use a local model as fallback", "fallback_local"), ("Switch everything to offline", "use_offline")])
    return _check(cid, area, "warn", f"{name} answered {detail}", "Possibly a temporary provider problem.")


def check_llms() -> list:
    out = []
    if not getattr(config, "CLEAN_WITH_LLM", True):
        return [_check("llm_primary", "Cleanup LLM", "info", "LLM cleanup is turned off",
                       "Text is pasted with basic punctuation only.", [("Turn cleanup on", "cleanup_on")])]
    primary = (getattr(config, "LLM_BACKEND", "groq") or "groq").lower()
    out.append(_llm_check("primary", primary))
    fb = (getattr(config, "LLM_FALLBACK_BACKEND", "") or "").lower()
    if fb:
        out.append(_llm_check("fallback", fb))
    elif primary != "ollama":
        out.append(_check("llm_fallback", "Fallback LLM", "warn", "No fallback LLM",
                          "If your main LLM is rate-limited or offline, dictations are pasted uncleaned. "
                          "A local model as fallback keeps cleanup working.",
                          [("Use a local model as fallback", "fallback_local")]))
    secondary = (getattr(config, "LLM_BRAIN_SECONDARY", "") or "").lower()
    if getattr(config, "LLM_HYBRID", False) and secondary and secondary != primary:
        out.append(_llm_check("hybrid", secondary))
    return out


def check_recent_dictations(limit: int = 20) -> dict:
    """What actually happened to the last dictations (the ground truth)."""
    import database as db
    try:
        rows = [r for r in db._get_all_rows(limit * 3) if r.get("status") == "success"][:limit]
    except Exception as e:
        return _check("recent", "Recent dictations", "info", "History unavailable", str(e)[:120])
    if not rows:
        return _check("recent", "Recent dictations", "info", "No dictations yet")
    skipped = [r for r in rows if (r.get("llm_status") or "").startswith("basic:")]
    if not skipped:
        return _check("recent", "Recent dictations", "ok", f"Last {len(rows)} dictations were cleaned")
    reasons: dict = {}
    for r in skipped:
        why = explain_llm_status(r["llm_status"])
        reasons[why] = reasons.get(why, 0) + 1
    top = max(reasons, key=reasons.get)
    # Only suggest adding a local fallback when there isn't one already —
    # offering it to someone who has it configured just reads as noise.
    has_fallback = bool((getattr(config, "LLM_FALLBACK_BACKEND", "") or "").strip())
    already_local = getattr(config, "LLM_BACKEND", "") == "ollama" or has_fallback
    fixes = [] if already_local else [("Use a local model as fallback", "fallback_local")]
    if "rate-limit" in top:
        fixes.append(("Switch everything to offline", "use_offline"))
    return _check("recent", "Recent dictations",
                  "error" if len(skipped) * 2 >= len(rows) else "warn",
                  f"{len(skipped)} of the last {len(rows)} dictations were pasted WITHOUT cleanup",
                  "Most often because " + top + ".", fixes)


def check_wake() -> list:
    if not getattr(config, "WAKE_WORD_ENABLED", False):
        return [_check("wake", "Wake word", "info", "Voice activation is off",
                       "Using hotkeys / mouse / double-tap only. Turn on ENABLED next to the wake phrase to use it.")]
    phrase = (getattr(config, "WAKE_WORD", "") or "").strip()
    if not phrase:
        return [_check("wake", "Wake word", "error", "Voice activation is on but no wake phrase is set",
                       "Type a phrase in Config → Wake Phrase (e.g. \"hey nova\").")]
    out = []
    if getattr(config, "WAKE_ENGINE", "asr") == "kws":
        import wake_engine
        st = wake_engine.status()
        if not st["available"]:
            out.append(_check("wake", "Wake word", "error", "Local wake engine selected but not installed",
                              "Run  pip install sherpa-onnx sentencepiece  and restart — until then the "
                              "wake word uses speech recognition.", [("Use speech-recognition wake word", "wake_asr")]))
        elif not st["model_ready"]:
            out.append(_check("wake", "Wake word", "error", "Wake-word model not downloaded",
                              "The local engine needs a one-time ~17 MB download.",
                              [("Download it now", "download_wake"), ("Use speech-recognition wake word", "wake_asr")]))
        else:
            out.append(_check("wake", "Wake word", "ok", f"Local wake engine listening for \"{phrase}\""))
    else:
        cloud = (getattr(config, "ASR_BACKEND", "groq") or "").lower() in _CLOUD_ASR
        out.append(_check("wake", "Wake word", "ok" if not cloud else "info",
                          f"Listening for \"{phrase}\" with speech recognition",
                          "Background sounds are sent to your cloud speech service to check for the phrase. "
                          "The local wake engine is faster and private." if cloud else "",
                          [("Use the local wake engine", "wake_kws")] if cloud else ()))
    try:
        import voice_enrollor
        enrolled = voice_enrollor.is_enrolled()
    except Exception:
        enrolled = False
    if enrolled and getattr(config, "VOICE_ENROLLMENT_ENABLED", False):
        out.append(_check("voice", "Voice Match", "ok", "Your voice is registered and required"))
    elif enrolled:
        out.append(_check("voice", "Voice Match", "info", "Your voice is registered but not required",
                          "Turn on \"Require my voice for commands\" to ignore other voices."))
    else:
        out.append(_check("voice", "Voice Match", "info", "Voice not registered (optional)",
                          "Anyone can trigger the wake phrase. Register in Config → Voice Match if others or the TV trigger it."))
    return out


def check_offline_readiness() -> dict:
    """Can NibCast work with no internet right now? Says exactly what's missing."""
    import local_asr
    missing = []
    if not local_asr.available():
        missing.append("pip install faster-whisper (speech-to-text)")
    state, _ = _local_llm_state()
    if state != "ok":
        import local_models
        servers = _probe("local:any", lambda: local_models.detect(timeout=0.8))
        if not any(s["models"] for s in (servers if isinstance(servers, list) else [])):
            missing.append("a local model server, e.g. Ollama + `ollama pull qwen2.5:7b` (cleanup)")
    if getattr(config, "OFFLINE_MODE", False):
        return _check("offline", "Offline readiness", "ok" if not missing else "error",
                      "Offline Mode is ON" + ("" if not missing else " — but something is missing"),
                      "" if not missing else "Still needed: " + "; ".join(missing) + ".")
    if missing:
        return _check("offline", "Offline readiness", "info", "Can't work fully offline yet",
                      "Needed: " + "; ".join(missing) + ".")
    return _check("offline", "Offline readiness", "ok", "Ready to work fully offline",
                  "Everything needed is installed.", [("Switch everything to offline", "use_offline")])


def recent_problems(limit: int = 8) -> list:
    out = [p for p in list(RECENT_PROBLEMS)[-limit:]]
    out.reverse()
    return out


def run_all(mic_level_at: float = 0.0, version_stale: bool = False) -> dict:
    # The network probes are independent — run them side by side so one
    # unreachable provider (4 s timeout) doesn't stack with the others.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(check_asr), pool.submit(check_llms), pool.submit(check_recent_dictations),
                pool.submit(check_wake), pool.submit(check_offline_readiness)]
        results = []
        for j in jobs:
            try:
                r = j.result(timeout=20)
            except Exception as e:
                r = _check("internal", "Health check", "warn", "A check failed to run", str(e)[:160])
            results.extend(r if isinstance(r, list) else [r])
    checks = []
    if version_stale:
        checks.append(_check("stale", "App", "error", "NibCast needs a restart",
                             "The code on disk is newer than the running app — new settings won't work "
                             "until you quit NibCast (tray → Quit) and start it again."))
    checks.append(check_microphone(mic_level_at))
    checks.extend(results)
    errors = sum(1 for c in checks if c["status"] == "error")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    worst = "error" if errors else "warn" if warnings else "ok"
    return {"overall": worst, "problems": errors + warnings, "errors": errors, "warnings": warnings,
            "checks": checks, "recent_log": recent_problems(), "checked_at": time.time()}


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ── One-click fixes ───────────────────────────────────────────

def _first_local_model():
    import local_models
    for s in local_models.detect(timeout=0.8):
        if s["models"]:
            return s["base_url"], s["models"][0]
    return None, None


def apply_fix(action: str) -> dict:
    """Apply a dashboard fix. Returns {"ok", "message"} — message says what changed
    or exactly what the user still has to do."""
    import local_asr
    import local_models
    clear_cache()
    if action == "use_offline":
        notes, changed = [], []
        if local_asr.available():
            config.ASR_BACKEND = "faster_whisper"
            local_asr.load_async()
            changed.append("speech-to-text → In-app Whisper")
        else:
            notes.append("install faster-whisper (pip install faster-whisper) for offline speech-to-text")
        base, model = _first_local_model()
        if model:
            config.OLLAMA_BASE_URL, config.OLLAMA_LLM_MODEL = base, model
            config.LLM_BACKEND = "ollama"
            local_models.warm_up_async(base, model)
            changed.append(f"cleanup → local model {model}")
        else:
            notes.append("start a local model server (e.g. Ollama + ollama pull qwen2.5:7b) for offline cleanup")
        if not notes:
            config.OFFLINE_MODE = True
            changed.append("Offline Mode on")
        config.save()
        msg = ("Changed: " + ", ".join(changed) + "." if changed else "") + \
              (" Still needed: " + "; ".join(notes) + "." if notes else "")
        return {"ok": not notes, "message": msg.strip()}
    if action == "use_online":
        if not (getattr(config, "GROQ_API_KEY", "") or ""):
            return {"ok": False, "message": "Add a free Groq API key first (Config → AI Backend, console.groq.com)."}
        config.OFFLINE_MODE = False
        config.ASR_BACKEND = "groq"
        config.LLM_BACKEND = "groq"
        config.save()
        return {"ok": True, "message": "Switched speech-to-text and cleanup to Groq (online), Offline Mode off."}
    if action == "offline_off":
        config.OFFLINE_MODE = False
        config.save()
        return {"ok": True, "message": "Offline Mode turned off — cloud services allowed again."}
    if action == "fallback_local" and getattr(config, "LLM_BACKEND", "") == "ollama":
        return {"ok": True, "message": "Your main cleanup already runs on a local model — "
                                       "no fallback needed for rate limits or outages."}
    if action in ("fallback_local", "detect_local"):
        base, model = _first_local_model()
        if not model:
            return {"ok": False, "message": "No local model server found. Install Ollama (ollama.com), run "
                                            "`ollama pull qwen2.5:7b`, then try again."}
        config.OLLAMA_BASE_URL, config.OLLAMA_LLM_MODEL = base, model
        if action == "fallback_local":
            config.LLM_FALLBACK_BACKEND = "ollama"
            msg = f"Local model {model} is now the fallback when the main LLM fails."
        else:
            config.LLM_BACKEND = "ollama"
            msg = f"Cleanup now uses the local model {model}."
        config.save()
        local_models.warm_up_async(base, model)
        return {"ok": True, "message": msg}
    if action == "load_whisper":
        if not local_asr.available():
            return {"ok": False, "message": "Run  pip install faster-whisper  and restart NibCast first."}
        local_asr.load_async()
        return {"ok": True, "message": "Loading In-app Whisper (first time downloads the model)…"}
    if action == "cleanup_on":
        config.CLEAN_WITH_LLM = True
        config.save()
        return {"ok": True, "message": "LLM cleanup turned on."}
    if action in ("wake_asr", "wake_kws", "download_wake"):
        import wake_engine
        if action == "download_wake":
            wake_engine.download_async()
            return {"ok": True, "message": "Downloading the wake-word model (~17 MB)…"}
        if action == "wake_kws" and not wake_engine.available():
            return {"ok": False, "message": "Run  pip install sherpa-onnx sentencepiece  and restart NibCast first."}
        config.WAKE_ENGINE = "kws" if action == "wake_kws" else "asr"
        config.save()
        if action == "wake_kws" and not wake_engine.model_ready():
            wake_engine.download_async()
        return {"ok": True, "message": "Wake engine switched — the change applies right away.", "wake_changed": True}
    log.warning(f"Unknown health fix action: {action}")
    return {"ok": False, "message": f"Unknown fix '{action}'."}
