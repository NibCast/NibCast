# ============================================================
#  NibCast — Auto-select the cleanup LLM  (config: LLM_AUTO_SELECT)
# ============================================================
#  With auto-select on, nobody has to pick a cleanup model. Every
#  dictation is cleaned by the few best candidates among the
#  providers you have set up (an API key is present, or the local
#  server answers), each result is scored for
#
#     fidelity — did it keep what you said, in your words?
#     polish   — grammar, punctuation, no fillers, complete ending
#
#  and the best one is pasted. Running per-model stats (kept in
#  ~/.nibcast/llm_auto_stats.json) decide which candidates run next
#  time, so NibCast settles on whatever gives YOU the best output and
#  stops waiting on engines that keep timing out or returning junk.
#
#  Models are taken only from what each provider currently lists
#  (GET /models, cached for a few hours), so a retired model name
#  drops out by itself instead of 404-ing every dictation.
#
#  Privacy: auto-select sends the transcript to every provider you
#  have configured (and only those). Restrict it with
#  LLM_AUTO_PROVIDERS, e.g. ["groq", "ollama"].
# ============================================================

from __future__ import annotations

import json
import os
import random
import threading
import time

import requests

import config
from logger import log

STATS_FILE = os.path.join(config.USER_DIR, "llm_auto_stats.json")

# Cleanup quality preference per provider, best first. Only ids the provider
# actually lists are used; your own configured model is always a candidate.
PREFERRED = {
    "groq":      ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905",
                  "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b",
                  "meta-llama/llama-4-maverick-17b-128e-instruct", "openai/gpt-oss-20b"],
    "cerebras":  ["gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b"],
    "gemini":    ["gemini-2.5-flash", "gemini-2.0-flash"],
    "openai":    ["gpt-4.1-mini", "gpt-4o-mini"],
    "nvidia":    ["meta/llama-3.3-70b-instruct", "openai/gpt-oss-120b"],
    "anthropic": [],
    "ollama":    [],
    "custom":    [],
}
# When a provider lists none of its preferred ids (catalogues get renamed),
# take the first listed model matching one of these strong general-purpose
# families instead of giving up on the provider.
GENERIC_GOOD = ["gpt-oss-120b", "llama-4-maverick", "llama-3.3-70b", "qwen3-235b",
                "kimi-k2", "deepseek-v3", "mistral-large", "llama-3.1-70b", "llama-3.1-405b"]
_NOT_CHAT = ("vision", "-vl", "guard", "embed", "reward", "whisper", "tts", "rerank",
             "safety", "parse", "coder", "base")
PROVIDER_ORDER = ["groq", "cerebras", "gemini", "openai", "anthropic", "nvidia", "ollama", "custom"]
_LOCAL = {"ollama", "custom"}
_MAX_PER_PROVIDER = 2
_MODELS_TTL = 6 * 3600
_FAIL_COOLDOWN = 600          # seconds an engine sits out after 3 straight failures
_MAX_COOLDOWN = 6 * 3600      # …doubling each further failure, up to this

_lock = threading.Lock()
_models_cache: dict = {}      # provider -> (timestamp, set[str] | None)
_stats: dict | None = None
_calls = 0
_last_cands: list = []
last_fail_reason = ""


# ── Stats ─────────────────────────────────────────────────────

def _load_stats() -> dict:
    global _stats
    if _stats is None:
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                _stats = json.load(f)
        except Exception:
            _stats = {}
    return _stats


def _save_stats():
    try:
        tmp = STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_stats or {}, f, indent=1)
        os.replace(tmp, STATS_FILE)
    except Exception as e:
        log.debug(f"auto-select stats not saved: {e}")


def _entry(key: str) -> dict:
    st = _load_stats()
    return st.setdefault(key, {"runs": 0, "ok": 0, "wins": 0, "score_sum": 0.0,
                               "fid_sum": 0.0, "lat_sum": 0.0, "fails": 0,
                               "consec_fail": 0, "last_fail": 0.0})


def summary() -> list:
    """Leaderboard for the dashboard / logs: best first."""
    with _lock:
        rows = []
        for k, e in _load_stats().items():
            ok = max(1, e["ok"])
            rows.append({"engine": k, "runs": e["runs"], "wins": e["wins"],
                         "avg_score": round(e["score_sum"] / ok, 1),
                         "avg_fidelity": round(e["fid_sum"] / ok, 2),
                         "avg_latency": round(e["lat_sum"] / ok, 2),
                         "fails": e["fails"]})
    return sorted(rows, key=lambda r: (-r["wins"], -r["avg_score"]))


# ── Discovery ─────────────────────────────────────────────────

def _models_url(chat_url: str) -> str:
    base = chat_url.rsplit("/chat/completions", 1)[0]
    return base.rstrip("/") + "/models"


def _list_models(provider: str, params: dict):
    """Model ids the provider serves right now, or None if unknown."""
    now = time.time()
    hit = _models_cache.get(provider)
    if hit and now - hit[0] < (_MODELS_TTL if hit[1] is not None else 300):
        return hit[1]
    ids = None
    if params.get("kind") == "openai_compat" and params.get("url"):
        try:
            headers = {"Authorization": f"Bearer {params['key']}"} if params.get("key") else {}
            r = requests.get(_models_url(params["url"]), headers=headers,
                             timeout=4 if provider in _LOCAL else 6)
            if r.status_code == 200:
                ids = {str(m.get("id", "")).removeprefix("models/")
                       for m in (r.json().get("data") or [])} - {""}
        except Exception:
            ids = None
    _models_cache[provider] = (now, ids)
    return ids


def _match(model: str, avail: set):
    if model in avail:
        return model
    tail = model.split("/")[-1]
    for a in avail:
        if a.split("/")[-1] == tail:
            return a
    return None


def candidates() -> list:
    """Every (provider, model) worth trying, in preference order."""
    import text_processor as tp
    allow = [p.strip().lower() for p in (getattr(config, "LLM_AUTO_PROVIDERS", []) or []) if p.strip()]
    out = []
    for prov in PROVIDER_ORDER:
        if allow and prov not in allow:
            continue
        params = tp._llm_params(prov)
        if params is None:
            continue
        configured = (getattr(config, "ANTHROPIC_LLM_MODEL", "") if prov == "anthropic"
                      else params.get("model", ""))
        avail = None if prov == "anthropic" else _list_models(prov, params)
        if avail is None and prov in _LOCAL:
            continue                      # local server isn't running
        models = []
        for m in PREFERRED.get(prov, []) + [configured]:
            if not m:
                continue
            real = (m if m == configured else None) if avail is None else _match(m, avail)
            if real and real not in models:
                models.append(real)
        if not models and avail:
            chat = sorted(a for a in avail if not any(x in a.lower() for x in _NOT_CHAT))
            for fam in GENERIC_GOOD:
                hit = next((a for a in chat if fam in a.lower()), None)
                if hit:
                    models.append(hit)
                    break
        out += [(prov, m) for m in models[:_MAX_PER_PROVIDER]]
    global _last_cands
    if out != _last_cands:
        _last_cands = out
        log.info("🤖 Auto-select engines available: " + (", ".join(f"{p}/{m}" for p, m in out) or "none"))
    return out


# ── Selection ─────────────────────────────────────────────────

def _rank(cands: list, max_wait: float) -> list:
    now = time.time()
    ranked = []
    with _lock:
        for idx, (prov, model) in enumerate(cands):
            e = _entry(f"{prov}/{model}")
            if e["consec_fail"] >= 3:
                cool = min(_MAX_COOLDOWN, _FAIL_COOLDOWN * 2 ** (e["consec_fail"] - 3))
                if now - e["last_fail"] < cool:
                    continue
            prior = -0.4 * idx
            if e["ok"] < 3:
                val = 6.0 + prior            # optimistic until measured
            else:
                avg = e["score_sum"] / e["ok"]
                lat = e["lat_sum"] / e["ok"]
                if lat > max_wait:           # consistently too slow to ever win
                    continue
                val = avg + 8.0 * e["wins"] / max(1, e["runs"]) - max(0.0, lat - 3.0) + prior
            ranked.append((val, prov, model))
    ranked.sort(key=lambda r: -r[0])
    return ranked


def _pick(ranked: list, k: int) -> list:
    """Top k, at most one per provider first (different engines disagree in
    useful ways), then fill; every 8th call swaps the last slot for a
    less-proven candidate so a newly listed model gets measured."""
    global _calls
    _calls += 1
    chosen, seen = [], set()
    for _, prov, model in ranked:
        if prov not in seen:
            chosen.append((prov, model)); seen.add(prov)
        if len(chosen) == k:
            break
    for _, prov, model in ranked:
        if len(chosen) >= k:
            break
        if (prov, model) not in chosen:
            chosen.append((prov, model))
    rest = [(p, m) for _, p, m in ranked if (p, m) not in chosen]
    if rest and len(chosen) >= 2 and _calls % 8 == 0:
        chosen[-1] = random.choice(rest)
    return chosen


def clean(system_prompt: str, raw_text: str):
    """Returns (text, provider, model); text is "" when nothing usable came back."""
    global last_fail_reason
    import text_processor as tp
    from hybrid import race_many

    last_fail_reason = ""
    k = max(1, int(getattr(config, "LLM_AUTO_CANDIDATES", 3)))
    max_wait = float(getattr(config, "LLM_AUTO_MAX_WAIT_SEC", 10.0))
    grace = float(getattr(config, "LLM_AUTO_GRACE_SEC", 2.0))

    ranked = _rank(candidates(), max_wait)
    chosen = _pick(ranked, k)
    if not chosen:
        last_fail_reason = "not_configured"
        log.warning("🤖 Auto-select: no usable cleanup engine (add an API key, or start Ollama)")
        return "", "", ""

    procs = [tp.TextProcessor() for _ in chosen]
    fns = [(lambda p=p, prov=prov, model=model:
            p._run_backend(system_prompt, raw_text, prov, allow_stream=False, model=model))
           for p, (prov, model) in zip(procs, chosen)]
    usable = lambda r: bool(r) and not tp._is_llm_mode_hallucination(raw_text, r)
    results = race_many(fns, timeout=max_wait, grace=grace, usable=usable)

    scored, parts = [], []
    with _lock:
        for (prov, model), proc, (res, secs) in zip(chosen, procs, results):
            key = f"{prov}/{model}"
            e = _entry(key)
            e["runs"] += 1
            if res and usable(res):
                sc = tp._score_cleanup(raw_text, res)
                fid = tp._fidelity(raw_text, res)
                e["ok"] += 1; e["score_sum"] += sc; e["fid_sum"] += fid
                e["lat_sum"] += secs or 0.0; e["consec_fail"] = 0
                scored.append((sc, fid, prov, model, res))
                parts.append(f"{key} score={sc:.1f} fid={fid:.2f} {secs:.1f}s")
            else:
                why = "slow" if res is None else (proc._fail_reason or "empty")
                e["fails"] += 1; e["consec_fail"] += 1; e["last_fail"] = time.time()
                parts.append(f"{key} — {why}")
                last_fail_reason = last_fail_reason or ("timeout" if why == "slow" else why)
        if scored:
            scored.sort(key=lambda s: (-s[0], -s[1]))
            _entry(f"{scored[0][2]}/{scored[0][3]}")["wins"] += 1
        _save_stats()

    if not scored:
        log.warning("🤖 Auto-select: no engine returned a usable cleanup | " + " | ".join(parts))
        return "", "", ""
    sc, fid, prov, model, text = scored[0]
    log.info(f"🤖 Auto-select → {prov}/{model} | " + " | ".join(parts))
    return text, prov, model
