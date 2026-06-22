# ============================================================
#  NibCast — Transcriber (multi-backend ASR)
# ============================================================
#  Backends: groq | openai | nvidia | local | custom
#
#  NOTE: NVIDIA's API (integrate.api.nvidia.com) does NOT
#  support audio transcription — their /v1/audio/transcriptions
#  returns 404 for all models. Use groq (free) or openai.
#
#  Brain Mode: runs two backends in parallel and returns
#  whichever produces the longer / more complete transcript.
# ============================================================

from __future__ import annotations   # lazy annotations: 'dict | None' won't eval-crash on Python <3.10

import re
import time
import threading
import requests

import config
from logger import log


# ── Deepgram diarization formatter ──────────────────────────
# Converts word-level speaker tags from Deepgram into a human-readable
# "Speaker 1: ...\nSpeaker 2: ..." transcript. Each speaker's words are
# grouped into continuous runs before being emitted as a labelled line.

def _format_diarized(alternative: dict) -> str:
    """Build a speaker-labelled transcript from Deepgram word-level diarization data."""
    words = alternative.get("words", [])
    if not words:
        return (alternative.get("transcript") or "").strip()

    lines = []
    current_speaker = -1   # -1 means "not yet seen any speaker"
    current_words: list[str] = []

    for w in words:
        speaker = w.get("speaker", 0)
        word    = w.get("punctuated_word") or w.get("word") or ""
        if speaker != current_speaker:
            if current_speaker is not None and current_words:
                lines.append(f"Speaker {current_speaker + 1}: {' '.join(current_words)}")
            current_speaker = speaker
            current_words   = [word]
        else:
            current_words.append(word)

    if current_speaker is not None and current_words:
        lines.append(f"Speaker {current_speaker + 1}: {' '.join(current_words)}")

    return "\n".join(lines)


# ── Whisper hallucination filter ─────────────────────────────
# Exact phrases Whisper commonly hallucinates for near-silence, noise
# bursts, or very short audio.  Extended from confirmed production data.

_HALLUCINATIONS = {
    # Near-silence / noise
    "thank you.", "thank you", "thanks for watching.",
    "thanks.", "you.", "you", ".", " ", "",
    "thanks for watching", "please subscribe.",
    "bye.", "bye bye.", "see you next time.",
    "see you next time", "i don't know.", "okay.", "okay",
    # Filler words alone
    "no.", "no", "yes.", "yes", "yeah.", "yeah",
    "hmm.", "hmm", "um.", "um", "uh.", "uh",
    "sorry.", "i'm sorry.", "excuse me.",
    "all right.", "alright.", "right.",
    # Video/stream artifacts
    "the end.", "end.",
    "music.", "[music]", "[applause]", "[laughter]", "[silence]",
    "subtitles by", "subtitles by the amara.org community",
    "www.movieweb.com",
    "like and subscribe.", "like and subscribe",
    "see you in the next video.", "see you in the next video",
    "thank you for watching.", "thank you for watching",
    "don't forget to subscribe.", "don't forget to subscribe",
    # Common short hallucinations
    "i love you.", "i love you",
    "hello.", "hello", "hi.", "hi",
    "one.", "two.", "three.", "one", "two", "three",
    "okay let's go.", "let's go.", "let's go",
    "oh.", "oh", "ah.", "ah",
}

_PUNCT_ONLY = re.compile(r'^[\s.,!?;:\-–—…]+$')

def _is_hallucination(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    if t in _HALLUCINATIONS:
        return True
    if _PUNCT_ONLY.fullmatch(t):
        return True
    return False


# ── Backend descriptors ──────────────────────────────────────

# Secondary backends we've already warned about (key missing) — so the warning
# is logged once, not on every utterance.
_warned_secondary: set = set()


def _backend_params(name: str, quiet: bool = False) -> dict | None:
    """Return (url, key, model) dict for the named backend, or None if unusable.

    `quiet=True` downgrades "not configured" messages from ERROR to DEBUG. Use it
    when probing an *optional* backend (e.g. the Brain-Mode second engine), so a
    user who simply hasn't added that key doesn't get an error logged on every
    single utterance — only a genuinely-broken primary should surface as an error.
    """
    _emit = log.debug if quiet else log.error
    n = name.strip().lower()
    if n == "groq":
        key = getattr(config, "GROQ_API_KEY", "")
        if not key:
            _emit("❌ GROQ_API_KEY not set — get a free key at console.groq.com")
            return None
        return {
            "url":   getattr(config, "GROQ_ASR_URL",   "https://api.groq.com/openai/v1/audio/transcriptions"),
            "key":   key,
            "model": getattr(config, "GROQ_ASR_MODEL", "whisper-large-v3-turbo"),
            "name":  "Groq Whisper",
        }
    elif n == "openai":
        key = getattr(config, "OPENAI_API_KEY", "")
        if not key:
            _emit("❌ OPENAI_API_KEY not set")
            return None
        return {
            "url":   getattr(config, "OPENAI_ASR_URL",   "https://api.openai.com/v1/audio/transcriptions"),
            "key":   key,
            "model": getattr(config, "OPENAI_ASR_MODEL", "whisper-1"),
            "name":  "OpenAI Whisper",
        }
    elif n == "nvidia":
        # NVIDIA's integrate.api.nvidia.com does NOT host an audio/transcription
        # endpoint — every request 404s. Treat NVIDIA as an unusable ASR backend
        # so transcribe() falls back to a working one (Groq/OpenAI) instead of
        # burning a request on a guaranteed 404. NVIDIA remains valid for LLM.
        _emit("❌ NVIDIA does not support ASR (no audio endpoint) — "
              "falling back. Use Groq (free) or OpenAI for transcription.")
        return None
    elif n == "local":
        return {
            "url":   getattr(config, "LOCAL_ASR_URL",   "http://localhost:8000/v1/audio/transcriptions"),
            "key":   "",
            "model": getattr(config, "LOCAL_ASR_MODEL", "whisper-1"),
            "name":  "Local ASR",
        }
    elif n == "deepgram":
        key = getattr(config, "DEEPGRAM_API_KEY", "")
        if not key:
            _emit("❌ DEEPGRAM_API_KEY not set — get a free key at console.deepgram.com")
            return None
        return {
            "url":   getattr(config, "DEEPGRAM_ASR_URL", "https://api.deepgram.com/v1/listen"),
            "key":   key,
            "model": getattr(config, "DEEPGRAM_ASR_MODEL", "nova-3"),
            "name":  "Deepgram",
            "kind":  "deepgram",
        }
    elif n == "custom":
        url = getattr(config, "CUSTOM_ASR_URL", "")
        if not url:
            _emit("❌ CUSTOM_ASR_URL not set")
            return None
        return {
            "url":   url,
            "key":   getattr(config, "CUSTOM_API_KEY",   ""),
            "model": getattr(config, "CUSTOM_ASR_MODEL", ""),
            "name":  "Custom ASR",
        }
    return None


def has_configured_backend() -> bool:
    """True if the configured ASR setup is likely to actually work — i.e. an API
    key is present for a keyed backend (groq/openai/deepgram/nvidia), or the user
    explicitly chose 'local'/'custom' and gave it the params it needs.

    A fresh clone with no API keys defaults to ASR_BACKEND='groq' with no key set;
    _backend_params() returns None for it, but the fallback loop in transcribe()
    still finds the keyless 'local' backend params (they're always non-None) and
    tries to call http://localhost:8000, which silently fails with no local server
    running. This check distinguishes "nothing is configured" from "ASR ran but
    found no speech" so the UI can show an actionable message.
    """
    # quiet=True: this is a capability *probe*, not an attempt to use a backend —
    # logging "OPENAI_API_KEY not set" at ERROR here just because we checked whether
    # it could be a fallback is misleading noise in an otherwise-working setup.
    primary = getattr(config, "ASR_BACKEND", "groq").strip().lower()
    if primary in ("local", "custom"):
        return _backend_params(primary, quiet=True) is not None
    if _backend_params(primary, quiet=True) is not None:
        return True
    return any(_backend_params(b, quiet=True) is not None for b in ("groq", "openai", "deepgram", "nvidia"))


class Transcriber:
    def transcribe(self, wav_bytes: bytes, initial_prompt: str = "",
                   model_override: str = "") -> str:
        """
        Transcribe audio.

        `initial_prompt` biases Whisper toward specific vocabulary.
        `model_override` swaps the ASR model for this call — used for Phase 1
        wake-word detection where accuracy matters more than latency.
        """
        if not wav_bytes:
            log.warning("Empty audio — skipping transcription")
            return ""

        brain_mode = getattr(config, "BRAIN_MODE", False)
        primary    = getattr(config, "ASR_BACKEND", "groq")
        secondary  = getattr(config, "ASR_BRAIN_SECONDARY", "openai") if brain_mode else ""

        log.info(f"📡 ASR: backend={primary}  brain={brain_mode}  {len(wav_bytes)/1024:.1f} KB")

        p = _backend_params(primary)
        if p is None:
            for fallback in ("groq", "openai", "local", "custom"):
                if fallback == primary:
                    continue
                p = _backend_params(fallback, quiet=True)   # probing alternatives — stay quiet
                if p:
                    log.warning(f"⚠️  Primary backend '{primary}' not configured — falling back to '{fallback}'")
                    break
            if p is None:
                log.error("❌ No ASR backend is configured. Open Config → AI Backend.")
                return ""

        # Override model for this call without mutating the shared params dict
        if model_override and p is not None:
            p = {**p, "model": model_override}
            log.info(f"  → ASR model override: {model_override}")

        if brain_mode and secondary and secondary != primary:
            return self._brain_transcribe(wav_bytes, p, secondary, initial_prompt)

        result = self._call(wav_bytes, p, initial_prompt)

        if not result and secondary and secondary != primary:
            log.warning(f"⚠️  Primary ASR failed — trying fallback '{secondary}'")
            s = _backend_params(secondary, quiet=True)
            if s:
                result = self._call(wav_bytes, s, initial_prompt)

        return result

    # ── Brain Mode: parallel execution ────────────────────────

    def _brain_transcribe(self, wav_bytes: bytes, primary: dict, secondary_name: str,
                          initial_prompt: str = "") -> str:
        s = _backend_params(secondary_name, quiet=True)
        if not s:
            # Warn once per secondary, not on every utterance (the old behaviour
            # logged an ERROR + WARNING for each clip when the chosen second engine
            # had no key — a wall of noise that hid real problems).
            if secondary_name not in _warned_secondary:
                _warned_secondary.add(secondary_name)
                log.warning(f"Brain secondary '{secondary_name}' has no API key — using "
                            f"primary engine only. Add its key, pick a different second "
                            f"engine, or turn off Brain Mode in Config → AI Backend.")
            return self._call(wav_bytes, primary, initial_prompt)

        results = [None, None]

        def run(idx, params):
            results[idx] = self._call(wav_bytes, params, initial_prompt)

        t1 = threading.Thread(target=run, args=(0, primary),  daemon=True)
        t2 = threading.Thread(target=run, args=(1, s),        daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=config.HTTP_TIMEOUT + 5)
        t2.join(timeout=config.HTTP_TIMEOUT + 5)

        if results[0] is None:
            log.warning(f"🧠 Brain: {primary['name']} thread timed out — no result")
        if results[1] is None:
            log.warning(f"🧠 Brain: {s['name']} thread timed out — no result")

        r1, r2 = results[0] or "", results[1] or ""
        log.info(f"🧠 Brain: [{primary['name']}]={len(r1)}ch  [{s['name']}]={len(r2)}ch")

        if not r1 and not r2:
            return ""
        if not r1: return r2
        if not r2: return r1

        # Prefer a real transcript over one engine's hallucination (e.g. Whisper
        # emitting "Thank you."/"Okay." on a faint clip) before falling back to
        # the richer (more-words = more-complete) transcript when both look real.
        h1, h2 = _is_hallucination(r1), _is_hallucination(r2)
        if h1 and not h2:
            log.info(f"🧠 Brain winner: {s['name']} ({primary['name']} looked like a hallucination)")
            return r2
        if h2 and not h1:
            log.info(f"🧠 Brain winner: {primary['name']} ({s['name']} looked like a hallucination)")
            return r1

        w1 = len(r1.split())
        w2 = len(r2.split())
        winner = r1 if w1 >= w2 else r2
        log.info(f"🧠 Brain winner: {w1} vs {w2} words → {primary['name'] if w1>=w2 else s['name']}")
        return winner

    # ── Single backend HTTP call ───────────────────────────────

    def _call(self, wav_bytes: bytes, p: dict, initial_prompt: str = "") -> str:
        kind = p.get("kind", "whisper")
        if kind == "deepgram":
            return self._call_deepgram(wav_bytes, p)
        return self._call_whisper(wav_bytes, p, initial_prompt)

    def _call_deepgram(self, wav_bytes: bytes, p: dict) -> str:
        """Deepgram Nova REST API — significantly more accurate than Whisper on natural speech.
        Supports speaker diarization: when DEEPGRAM_DIARIZE=True, output is formatted as
        'Speaker 1: ...\nSpeaker 2: ...' so each person's words are clearly separated.
        """
        url   = p["url"]
        key   = p.get("key", "")
        model = p.get("model", "nova-3")
        name  = p.get("name", "Deepgram")
        diarize = getattr(config, "DEEPGRAM_DIARIZE", False)

        params = {"model": model, "smart_format": "true", "punctuate": "true"}
        if config.LANGUAGE:
            params["language"] = config.LANGUAGE
        if diarize:
            params["diarize"] = "true"

        headers = {"Content-Type": "audio/wav"}
        if key:
            headers["Authorization"] = f"Token {key}"

        retries = max(1, int(config.HTTP_RETRIES))
        for attempt in range(retries):
            try:
                resp = requests.post(
                    url, headers=headers, params=params,
                    data=wav_bytes, timeout=config.HTTP_TIMEOUT,
                )
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        alts = (body.get("results", {})
                                    .get("channels", [{}])[0]
                                    .get("alternatives", [{}]))
                        if not alts:
                            text = ""
                        elif diarize:
                            text = _format_diarized(alts[0])
                        else:
                            text = (alts[0].get("transcript") or "").strip()
                    except Exception as _parse_err:
                        log.warning(f"⚠️  [{name}] JSON parse failed: {_parse_err} — body: {resp.text[:200]}")
                        text = ""
                    log.info(f"✅ [{name}] {text!r}")
                    return text
                if 400 <= resp.status_code < 500:
                    log.error(f"❌ [{name}] HTTP {resp.status_code}: {resp.text[:300]}")
                    return ""
                log.warning(f"⚠️  [{name}] {resp.status_code} (attempt {attempt+1}/{retries})")
            except requests.exceptions.Timeout:
                log.warning(f"⚠️  [{name}] Timeout (attempt {attempt+1}/{retries})")
            except requests.exceptions.ConnectionError as e:
                log.warning(f"⚠️  [{name}] Connection error: {e}")
            except Exception as e:
                log.error(f"❌ [{name}] Error: {e}")
                return ""
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        log.error(f"❌ [{name}] Failed after all retries")
        return ""

    def _call_whisper(self, wav_bytes: bytes, p: dict, initial_prompt: str = "") -> str:
        url   = p["url"]
        key   = p.get("key", "")
        model = p.get("model", "")
        name  = p.get("name", "ASR")

        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        translate = getattr(config, "TRANSLATE_TO_ENGLISH", False)
        data = {"response_format": "json"}
        if translate:
            # OpenAI-compatible Whisper APIs (Groq, OpenAI) translate to English via a
            # dedicated /audio/translations endpoint — they reject a `task` param on
            # the transcriptions endpoint ("unknown param `task`", HTTP 400). Swap the
            # endpoint instead of sending the unsupported parameter.
            url = url.replace("/audio/transcriptions", "/audio/translations")
        if model:
            data["model"] = model
        # Only pass language when NOT translating and a language is set.
        if config.LANGUAGE and not translate:
            data["language"] = config.LANGUAGE
        # initial_prompt overrides config WHISPER_PROMPT when provided
        prompt = initial_prompt.strip() or getattr(config, "WHISPER_PROMPT", "").strip()
        if prompt:
            data["prompt"] = prompt

        retries = max(1, int(config.HTTP_RETRIES))
        for attempt in range(retries):
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data=data,
                    timeout=config.HTTP_TIMEOUT,
                )
                if resp.status_code == 200:
                    try:
                        text = resp.json().get("text", "").strip()
                    except Exception:
                        text = resp.text.strip()
                    log.info(f"✅ [{name}] {text!r}")
                    return text

                if 400 <= resp.status_code < 500:
                    snippet = resp.text[:300]
                    log.error(f"❌ [{name}] HTTP {resp.status_code}: {snippet}")
                    if resp.status_code == 404:
                        log.error(f"   Hint: endpoint not found → {url}")
                        if "nvidia" in url.lower():
                            log.error("   NVIDIA does not support audio transcription via this API.")
                            log.error("   Switch to Groq (free) in Config → AI Backend.")
                    return ""

                log.warning(f"⚠️  [{name}] {resp.status_code} (attempt {attempt+1}/{retries})")

            except requests.exceptions.Timeout:
                log.warning(f"⚠️  [{name}] Timeout (attempt {attempt+1}/{retries})")
            except requests.exceptions.ConnectionError as e:
                log.warning(f"⚠️  [{name}] Connection error: {e}")
            except Exception as e:
                log.error(f"❌ [{name}] Error: {e}")
                return ""

            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        log.error(f"❌ [{name}] Failed after all retries")
        return ""
