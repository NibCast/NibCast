# NibCast — Features

A complete tour of what NibCast does. NibCast is a privacy-first AI voice
dictation app for Windows: speak anywhere, and your cleaned-up words are pasted
into whatever window you're focused on. You bring your own API keys — there's no
subscription and no proprietary cloud.

> The control panel lives at **http://localhost:7171** and has three tabs —
> **Status**, **Insights**, and **Config** — plus per-app **Modes**.

---

## Capturing your voice

NibCast offers four ways to start dictating, mixable to taste:

| Method | How it works |
|---|---|
| **Hold to talk** | Hold a hotkey, speak, release — text is pasted. |
| **Toggle** | Press once to start, again to stop. |
| **Hands-free** | A hotkey arms continuous dictation. |
| **Voice activation** | Say a **wake word** ("hey jarvis", "hey hulk", …) and dictate hands-free. |
| **Mouse button** | Hold a mouse thumb button (X1/X2) or the middle button to talk. |
| **Double-tap** | Tap Right Ctrl / Right Alt / Right Shift / Caps Lock twice to start, twice to paste. |

### Local wake-word engine
Pick **WAKE ENGINE → Local keyword spotter** (Config → Wake Phrase) and NibCast
listens for your typed phrase with a tiny on-device model — instant, offline, and
nothing is recorded or uploaded until you say it. A sensitivity slider trades
false wakes against missed ones. With the speech-recognition engine instead,
**SUGGEST FROM HISTORY** learns how your phrase keeps being misheard.

### While you dictate
- **Live text** — a caption above the widget shows what's being heard.
- **Pause music** — playing media pauses and resumes automatically.
- **Screen context** (optional) — names visible in the active window are spelled right.

Wake-word listening is a two-phase engine: a low-cost local energy detector
wakes the cloud transcriber only when it hears speech, then a fuzzy match
confirms your wake phrase before it starts recording your command — so ambient
TV or background chatter doesn't trigger it.

### Reliable across machines
- **Adaptive silence detection** — the command gate tracks your microphone's
  actual noise floor, so quiet mics are no longer cut off mid-sentence.
- **Mic-gain-aware wake gate** with a safety ceiling, so a normal speaking voice
  always clears the threshold (no more "it never hears me").
- **Stable audio capture** (fixed block size + buffering) to eliminate the input
  overflow that made the wake word choppy on some laptops.
- Toggle voice activation on/off **live** from the dashboard — no restart.

---

## Accuracy: ASR + LLM cleanup

Every dictation runs through two stages — speech recognition, then a silent LLM
cleanup that fixes grammar, punctuation, capitalization, and filler words while
**preserving exactly what you said**.

### Custom dictionary
Add your names, jargon, and product terms once (Config → `WHISPER_PROMPT`). Those
spellings now bias **both** stages: the transcriber is primed to hear them, and
the cleanup LLM corrects obvious sound-alikes — e.g. "cloud code" → **Claude
Code**, "deep gram" → **Deepgram**, "nib cast" → **NibCast** — without ever
inserting a term you didn't say.

### Paste-ready formatting
Spoken lists become numbered lists, items that share details become a table in
apps that render Markdown (Notion, Obsidian, code editors, AI chats), and fillers,
padding sentences, stutters, false starts and self-corrections are removed. In
ChatGPT / Claude / Gemini windows the **AI Prompt** mode keeps your request in
your own words; emails get greeting / body / sign-off on their own lines.

### Writing styles
Choose how the cleanup reshapes your speech:
- **Flow** — clean prose, light restructuring (default)
- **Verbatim** — minimal touch; only filler removal + punctuation
- **Professional** — formal, polished
- **Concise** — trimmed to the essential point

### Hybrid / Brain Mode (optional)
Run two engines in parallel and automatically keep the better result — for
transcription (e.g. Groq Whisper + In-app Whisper) and, separately, for cleanup
(e.g. Groq + a local model). The first good answer wins after a short grace
window, so a slower local engine never delays a paste. Opt-in; single-engine
**Groq (free tier)** remains the zero-cost default.

---

## Bring-your-own backends

Everything is swappable, and your audio only ever goes to the provider **you**
choose.

- **Transcription (ASR):** Groq Whisper (free), OpenAI, Deepgram, **In-app
  Whisper** (runs inside NibCast, fully offline), a local Whisper server, or any
  custom endpoint.
- **Cleanup (LLM):** Groq, Cerebras, Google Gemini, OpenAI, Anthropic, NVIDIA
  NIM, **local models** (Ollama, LM Studio, llama.cpp, Jan — found by DETECT
  LOCAL MODELS), or custom. A **fallback** LLM takes over when the primary is
  rate-limited.
- **Offline Mode** guarantees nothing leaves your PC.
- **Streaming** output and multi-language support.

---

## Per-app Modes

NibCast detects the focused application and adapts. Built-in targets include
**Terminal**, **Code Editor**, **Browser**, **Chat**, **Email**, **Notes**, and a
**Generic** fallback — each with its own injection method and cleanup hint (e.g.
chat messages drop the trailing period; code editors paste verbatim). Auto-detect
picks the right one, or you can pin a target.

---

## The floating widget

A small always-on overlay shows the live state — idle, listening, recording,
processing — and can be shaped as an **orb**, **bar**, or **chip**, with **wave**,
**orbit**, or **pulse** animations. It's draggable and click-through-friendly.

---

## Insights & history

- **Status** tab: live state, recent transcriptions, and the active wake word.
- **Insights** tab: words today / this week / per session, most-used app, peak
  hour, success rate, an activity heatmap, vocabulary richness, and tone.
- **History** is stored locally in SQLite and can be exported to **CSV** or
  **PDF**, backed up, or cleared at any time.

---

## Appearance

Three built-in themes (**Phosphor**, **Foundry**, **Meridian**), selectable accent
colors, UI/display fonts, a light-mode option, and toggleable visual effects.

---

## Privacy by design

- All settings, history, and credentials live under `~/.nibcast/` — **never** in
  the project folder, and never committed.
- Audio and text are sent **only** to the API endpoints you configure with your
  own keys. There is no NibCast server in the middle.
- The dashboard is protected by a local PIN / pattern / TOTP.

See [PRIVACY.md](PRIVACY.md) for the full data-handling details and
[CHANGELOG.md](CHANGELOG.md) for what's new in each release.
