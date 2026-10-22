# ============================================================
#  NibCast — Local model discovery (no API keys)
# ============================================================
#  Finds LLM servers already running on this machine and lists
#  their models, so cleanup can run fully offline with one click:
#     Ollama (11434) · LM Studio (1234) · llama.cpp server (8080)
#     Jan (1337) · LocalAI / vLLM (8000)
#  All of them speak the OpenAI chat-completions API, which the
#  "ollama" LLM backend already uses — so a detected server is
#  used by pointing OLLAMA_BASE_URL / OLLAMA_LLM_MODEL at it.
#
#  Probes 127.0.0.1 only (never the network). "localhost" can
#  resolve to ::1 first on Windows, which servers bound to IPv4
#  answer with a slow connection refusal.
# ============================================================

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests

from logger import log

LOCAL_SERVERS = (
    ("Ollama",          11434),
    ("LM Studio",       1234),
    ("llama.cpp",       8080),
    ("Jan",             1337),
    ("LocalAI / vLLM",  8000),
)

# Model ids that can't do chat cleanup (speech, embedding, rerank servers often
# share these ports — e.g. a local whisper server on 8000).
_NON_CHAT = ("whisper", "embed", "rerank", "tts", "clip", "bge-", "nomic-embed")

# Rough preference for transcript cleanup: instruction-tuned general models
# that follow "return only the cleaned text" well at small sizes.
_PREFERRED = ("qwen3", "qwen2.5", "llama3.3", "llama3.2", "llama3.1", "gemma3",
              "gemma2", "mistral", "phi4", "phi3")


def _is_chat_model(model_id: str) -> bool:
    m = model_id.lower()
    return not any(tag in m for tag in _NON_CHAT)


def _rank(model_id: str) -> int:
    m = model_id.lower().replace("-", "").replace(":", "")
    for i, p in enumerate(_PREFERRED):
        if p.replace("-", "") in m:
            return i
    return len(_PREFERRED)


def _probe(name: str, port: int, timeout: float) -> dict | None:
    root = f"http://127.0.0.1:{port}"
    models: list = []
    try:
        r = requests.get(f"{root}/v1/models", timeout=timeout)
        if r.status_code == 200:
            models = [m.get("id", "") for m in (r.json().get("data") or []) if m.get("id")]
        elif port == 11434:
            raise ValueError("old Ollama")
        else:
            return None
    except Exception:
        if port != 11434:
            return None
        # Ollama versions before the OpenAI-compatible /v1/models route.
        try:
            r = requests.get(f"{root}/api/tags", timeout=timeout)
            if r.status_code != 200:
                return None
            models = [m.get("name", "") for m in (r.json().get("models") or []) if m.get("name")]
        except Exception:
            return None
    models = sorted((m for m in models if _is_chat_model(m)), key=lambda m: (_rank(m), m))
    return {"name": name, "port": port, "base_url": f"{root}/v1", "models": models}


def detect(timeout: float = 0.8) -> list:
    """Probe every known local server in parallel. Returns the reachable ones as
    [{"name", "port", "base_url", "models": [best first]}] in LOCAL_SERVERS order."""
    found: dict = {}

    def run(name, port):
        res = _probe(name, port, timeout)
        if res:
            found[port] = res

    threads = [threading.Thread(target=run, args=s, daemon=True) for s in LOCAL_SERVERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 0.5)
    return [found[p] for _, p in LOCAL_SERVERS if p in found]


def is_ollama_url(base_url: str) -> bool:
    try:
        return urlparse(base_url).port == 11434
    except ValueError:
        return False


def warm_up(base_url: str, model: str, keep_alive: str = "30m") -> bool:
    """Load the model into memory now, so the first dictation doesn't wait for it
    (a cold 7B model can take 5–20 s to load on CPU). On Ollama, also ask it to
    keep the model resident for `keep_alive` instead of its 5-minute default —
    dictation is bursty, and every unload costs the next paste that load time."""
    if not base_url or not model:
        return False
    try:
        if is_ollama_url(base_url):
            root = base_url.rstrip("/")
            root = root[:-3] if root.endswith("/v1") else root
            r = requests.post(f"{root}/api/generate",
                              json={"model": model, "keep_alive": keep_alive},
                              timeout=120)
        else:
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions",
                              json={"model": model, "max_tokens": 1,
                                    "messages": [{"role": "user", "content": "hi"}]},
                              timeout=120)
        ok = r.status_code == 200
        log.info(f"🔥 Local LLM warm-up {'done' if ok else f'failed (HTTP {r.status_code})'}: {model}")
        return ok
    except Exception as e:
        log.warning(f"🔥 Local LLM warm-up failed: {e}")
        return False


_last_warm = 0.0


def warm_up_async(base_url: str, model: str):
    global _last_warm
    _last_warm = time.time()
    threading.Thread(target=warm_up, args=(base_url, model), daemon=True,
                     name="LocalLLMWarmup").start()


def local_llm_in_use() -> bool:
    import config
    return "ollama" in (getattr(config, "LLM_BACKEND", ""),
                        getattr(config, "LLM_FALLBACK_BACKEND", ""),
                        getattr(config, "LLM_BRAIN_SECONDARY", ""))


def warm_if_stale(max_age_sec: float = 20 * 60):
    """Re-warm the local model if it may have been unloaded (Ollama keeps it for
    30 min after our warm-up). Cheap no-op when recently warmed or unused."""
    import config
    if local_llm_in_use() and time.time() - _last_warm > max_age_sec:
        warm_up_async(getattr(config, "OLLAMA_BASE_URL", ""), getattr(config, "OLLAMA_LLM_MODEL", ""))
