# NibCast — Privacy & Data Flow

_Last updated: 2026-06-19_

NibCast is a local Windows application. It has **no backend servers operated by
the project**, performs **no telemetry or analytics**, and collects nothing about
you. This document explains exactly what data exists, where it is stored, and what
leaves your computer.

> This is a plain-language description of how the software behaves, not a legal
> contract. The software is provided under the [MIT License](LICENSE) "AS IS".

## What stays on your machine

All NibCast data lives under `~/.nibcast/` on your own computer:

| File | Contents |
|---|---|
| `config.json` | Your settings, including API keys you enter |
| `history.db` | SQLite history of past transcriptions (text only) |
| `voice_profile.json` | Lightweight acoustic features for wake-word speaker check |
| `nibcast.log` | Diagnostic log (transcript text is excluded in Privacy Mode) |
| `.vf_auth`, `.vf_secret` | Hashed dashboard PIN and session secret |

- **Audio is never written to disk.** Recordings exist only in memory long enough
  to be transcribed, then are discarded.
- The web dashboard is bound to **localhost (127.0.0.1) only** and is protected by
  a PIN, draw-pattern, or TOTP. It is not reachable from your network.
- Dashboard fonts are **self-hosted** inside the app — opening the dashboard makes
  **no request to Google or any third party**.

## What leaves your machine — and only when you choose it

NibCast cannot transcribe or clean text locally unless you select a local backend.
When you configure a **cloud** backend, data is sent to **that provider**, using
**your own API key**:

| Data | Sent to | When |
|---|---|---|
| Your spoken **audio** | The **ASR** provider you select — Groq, OpenAI, or Deepgram | Each time you dictate |
| Your **transcript text** | The **LLM** provider you select — Groq, Cerebras, Gemini, OpenAI, NVIDIA, Anthropic, or Ollama | When LLM cleanup is enabled |
| App version string | `api.github.com` | Optional update check at startup |

Once data reaches a provider, it is governed by **that provider's** privacy policy,
retention, and acceptable-use terms — not by NibCast. Review the policy of whichever
provider you enable.

### ⚠ Free tiers may train on your data

Many providers' **no-credit-card free tiers are funded by using your inputs to
improve their models.** In NibCast's case that means the text you dictate (and for
audio backends, your voice) could be retained and used for training. This is
explicitly the case for **Google Gemini's free tier**, and commonly applies to
other free tiers too.

- For **sensitive dictation**, prefer a **paid** API key (paid tiers generally do
  *not* train on your data), a **local** backend (local Whisper + Ollama — nothing
  leaves your machine), or providers whose free tier excludes training.
- NibCast surfaces this warning **in the dashboard** next to the affected backend
  (e.g. a prominent notice on the Gemini option) so the trade-off is visible at the
  moment you choose it.
- This is a property of the **provider**, not of NibCast — always check the current
  terms of whichever provider and tier you enable.

**To keep everything fully offline**, choose a local ASR backend (local Whisper
server) and a local LLM backend (Ollama). In that configuration nothing except the
optional GitHub version check ever leaves your machine, and you can disable that too.

## Your controls

- **Privacy Mode** — excludes transcript text from all logs and history.
- **History auto-delete** — automatically removes entries older than N days.
- **Clear history** — wipe `history.db` from the dashboard at any time.
- **Delete everything** — remove the `~/.nibcast/` folder to erase all local data.

## Responsible use

Voice dictation can capture other people's speech. Recording-consent laws vary by
jurisdiction. You are responsible for using NibCast only where you are permitted to,
and for complying with the terms of any provider you connect it to.

## Contact

Questions or concerns: open an issue on the project's GitHub repository.
