# Changelog

All notable changes to NibCast are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [2.7.1] — 2026-10-22

"It doesn't work and I can't tell why" now always has an answer.

### Added
- **System Health** (top of the Status tab). Checks the microphone, speech-to-
  text, the cleanup LLM, the fallback and hybrid engines, what actually happened
  to your last 20 dictations, the wake word, Voice Match and offline readiness —
  and explains each problem in plain words: missing or rejected API key, no
  internet, provider rate-limiting you, local model server not running or
  missing the model, Offline Mode blocking a cloud engine, NibCast needing a
  restart after an update… Each problem has **one-click fixes**, and two global
  buttons: **GO OFFLINE** (In-app Whisper + a local model + Offline Mode — or a
  precise list of what's still missing) and **GO ONLINE** (Groq for both).
  Re-checks every minute; a red **⚠ PROBLEMS** button appears in the header
  when something is broken. Recent warnings/errors are listed underneath.
- **Every uncleaned paste says why.** The widget shows *NOT CLEANED* and the
  dashboard shows the reason (e.g. "the cleanup provider is rate-limiting
  you") — no error sound, since the paste itself worked.

### Changed
- **Voice Match (register your voice) is always visible** in Config — it used
  to appear only after voice activation was switched on. A link under the wake
  phrase jumps to it, and it states clearly whether your voice is registered,
  enforced, and what's still needed. It's optional: the wake word works without it.
- The dashboard header shows the running version instead of a fixed "v2.4".

---

## [2.7.0] — 2026-10-22

### Added
- **Local wake-word engine.** Config → Wake Word → *Local keyword spotter*
  listens for your typed phrase on this PC with a tiny streaming model
  (sherpa-onnx, ~17 MB, one-time download, no training). Instant, works
  offline, and nothing is recorded or uploaded until you say the phrase — the
  old engine recorded every noise and asked the speech recogniser. Sensitivity
  slider (strict ↔ eager). Needs `pip install sherpa-onnx sentencepiece`.
- **Live text while you speak.** A caption above the widget shows what NibCast
  is hearing, refreshed every second with In-app Whisper (free, private), or
  — opt-in — every 2.5 s with your cloud ASR on a short tail window. Display
  only; the pasted text still comes from the full transcription.
- **Pause music while dictating.** Spotify/YouTube/VLC… are paused when you
  start (or when the wake word opens the command window) and exactly those
  players resume afterwards. Uses Windows media sessions, so it never starts
  a player that wasn't playing. Needs `pip install winsdk`.
- **Screen context (optional, off by default).** Reads names and terms from the
  active window — its title, plus visible text via Windows' built-in on-device
  OCR — and uses them to spell what you say correctly (never to insert words
  you didn't say). The screenshot never leaves the PC and isn't saved.

### Changed
- **Hybrid cleanup is its own opt-in** (`LLM_HYBRID`, set by the HYBRID CLEANUP
  picker or AS HYBRID). It no longer turns on with Brain Mode, which only
  doubles up *transcription* — so adding a local cleanup model can't start
  uploading audio to a second cloud ASR. Existing setups keep their behaviour.
- AI Prompt detection runs after chat/email/notes, so "RE: Gemini launch –
  Outlook" stays an email; Gmail in a browser is now formatted as email.

### Fixed (from code + security review)
- **Offline Mode didn't cover Anthropic** — cleanup, hybrid and command mode
  could still reach api.anthropic.com. Every LLM entry point now checks it.
- Repeated proper names ("Bora Bora", "New York, New York") were collapsed
  as stutters; ".NET" lost its dot and "UM" (an acronym) was deleted.
- Spoken-list formatting no longer turns ordinary prose ("the first quarter
  was good, the second…") into a broken list; stretched fillers ("uuh",
  "errm") are removed again.
- Hybrid: failure reasons (rate-limited, timeout) are recorded instead of
  "empty"; neither engine streams to the dashboard (the loser used to keep
  streaming after the winner was pasted); the fallback LLM still runs if both
  hybrid engines fail; the local model is re-warmed so it isn't cold.
- Command-mode history rows record their own outcome.
- Changing the In-app Whisper model/device takes effect on Save.
- Local wake: the command right after the phrase ("hey nova send Bob the
  report") is no longer cut off; double-tap Right Alt works on AltGr layouts.
- Media pause/resume run in order with timeouts, so music is never left paused.
- Debug bundle: multi-line dictations from older logs, wake-match details and
  snippet expansions are scrubbed too; Privacy Mode redacts them in new logs.
- Screen OCR works for windows on a second monitor.
- History database connections are now always closed (they were leaked on
  every read/write, keeping the file locked on Windows).

---

## [2.6.0] — 2026-10-22

Paste-ready text: dictation comes out grammatical and formatted the way you
were speaking — lists as numbered lists, tabular details as tables — with
cloud and local models working together.

### Added
- **Hybrid cloud + local.** Brain Mode is now *Hybrid*: run two engines at
  once — e.g. Groq + a local model for cleanup, or Groq + In-app Whisper for
  transcription — and keep the better result. New **HYBRID CLEANUP** picker
  and an **AS HYBRID** button in DETECT LOCAL MODELS. The first usable answer
  is taken after a short grace window (`BRAIN_GRACE_SEC`, 1.5 s), so a slower
  local model never holds up a paste. History records which engine won
  (`hybrid:<backend>`).
- **Formatting by intent.** The cleanup prompt now turns spoken lists, steps
  and "first… second…" / "one… two…" sequences into numbered lists, obeys
  "bullet points" / "new line" / "new paragraph", and turns several items that
  share the same details (plans with prices, days with tasks) into a
  **Markdown table** — only in apps that render Markdown (Notion, Obsidian,
  code editors, AI chats); elsewhere a numbered list with the details.
- **AI Prompt mode.** ChatGPT, Claude, Gemini, Copilot, Perplexity and
  DeepSeek windows (desktop or browser tab) are detected: your request is kept
  in your own voice (never answered), every detail and constraint kept,
  multi-part asks numbered.
- **Email layout** — a greeting and sign-off you dictate go on their own lines
  with the body in short paragraphs (never invented). **Chat** stays short and
  casual and keeps @mentions.
- **Numbered lists without the LLM.** Even when cleanup is skipped (offline,
  rate-limited), "first … second … third …" and "number one … number two …"
  become a numbered list, and pure disfluencies (um, uh, erm, hmm) are removed.

### Changed
- **Stronger, grammatical cleanup.** The Flow prompt now removes hedging
  fillers ("kind of", "sort of", "like", "actually", "just" when used as
  filler), padding sentences ("So yeah.", "That's it.", "Let me think."),
  and fixes grammar fully (agreement, tense, articles, word order), with
  worked examples so small local models follow it too.
- Hybrid scoring prefers the candidate with no leftover stutters or
  disfluencies, proper sentence capitalisation, and the list/table format the
  speaker asked for; table pipes and list numbers no longer count as padding.

### Fixed
- Privacy Mode: the text-injection log line ("📋 Injecting: …") still contained
  the dictated text; it is now redacted, and scrubbed from Debug Bundles.

---

## [2.5.0] — 2026-10-22

Local-first: NibCast can now run with **no API keys and nothing leaving the
PC**, and start dictating without awkward key chords.

### Added
- **In-app Whisper (fully offline speech-to-text).** New ASR backend
  *In-app ★ Offline* runs faster-whisper inside NibCast — no server to start,
  no key. Pick a model (`tiny.en` … `large-v3-turbo`), click
  **DOWNLOAD / LOAD**; it downloads once to `~/.nibcast/models` and uses an
  NVIDIA GPU automatically when present (int8 CPU otherwise). Because it's
  local, **wake-word checks are private too** — Phase-1 clips are no longer
  uploaded. Optional dependency: `pip install faster-whisper` (bundled into the
  .exe when installed at build time).
- **DETECT LOCAL MODELS.** Config → AI Backend finds Ollama, LM Studio,
  llama.cpp server, Jan and LocalAI running on this PC, lists their chat
  models (embedding/speech models filtered out, best cleanup model first) and
  sets one as the primary cleanup engine or the **fallback** in one click. The
  former "Ollama" backend is now **Local ★ Offline** and works with any of
  them. The chosen model is warmed up at startup (and kept loaded for 30 min
  on Ollama) so the first dictation doesn't wait for it to load.
- **Offline Mode** — a hard guard: audio and text may only go to engines on
  this machine; cloud backends are refused even if selected or set as
  fallback.
- **Mouse push-to-talk.** Hold a mouse thumb button (X1/X2) or the middle
  button to talk, or set it to click-to-start/stop. On Windows the button's
  normal action (browser Back/Forward) is suppressed while assigned.
- **Double-tap to dictate.** Tap Right Ctrl, Right Alt/AltGr, Right Shift or
  Caps Lock twice to start, twice again to paste. Shortcuts and typing never
  trigger it — any other key during or between the taps cancels.
- Mouse and double-tap triggers apply as soon as you save — no restart. The
  global mouse hook only runs while a button is assigned.

### Fixed
- **Wake word on non-Groq ASR backends.** The Phase-1 wake check always sent
  Groq's model name (`whisper-large-v3`) to whatever backend was selected, so
  OpenAI and custom servers rejected every wake clip. The override now only
  applies to Groq.

---

## [2.4.3] — 2026-10-22

Driven by real daily-use history: many dictations were being pasted
**without** LLM cleanup and nothing said so, and most wake-word triggers were
discarded — many of them the wake phrase itself, misheard the same way every time.

### Fixed
- **Rate-limited cleanup is retried instead of silently skipped.** A 429 from
  the LLM (e.g. Groq's free tokens-per-minute cap) used to fall straight
  through to basic cleanup, pasting the raw transcript. NibCast now waits for
  the server's `Retry-After` (up to 8 s) and retries, then tries your
  `LLM_FALLBACK` backend, and only then falls back to basic cleanup.
- **Skipped cleanups are now visible.** Every history row records how its text
  was cleaned (`llm`, `fallback:<backend>`, `off`, or `basic:<reason>` —
  rate_limited, timeout, connection, guard, …). The Log shows an amber
  **UNCLEANED** badge on those rows, the log names the reason, and the Dev CSV
  export has a new **LLM Status** column. (Database schema v3; migrates
  automatically.)
- **Doubled words and repeated phrases are removed even without the LLM.**
  The old filter only caught a word repeated 3+ times, so "updated updated",
  "I I" and "give me the, give me the" were pasted as-is. Two-word stutters,
  comma-separated runs ("it's, it's, it's") and restarted 2–5-word phrases now
  collapse, while intentional doubles ("had had", "very very") stay — even
  before the LLM runs.
- **The anti-chatbot guard no longer throws away correct cleanups** of
  dictation that genuinely starts with "Here is…", "Based on…", "It looks
  like…", "Sure, …". When the guard does fire, the fallback LLM is tried before
  basic cleanup.
- **Saying the wake phrase again during the command window re-arms it**
  instead of pasting "Hey nova hey nova" into your document.
- Whisper's foreign-language YouTube outros on background noise ("Hãy
  subscribe cho kênh … để không bỏ lỡ những video hấp dẫn") are now treated as
  hallucinations.
- Run-at-startup registered `main.py` relative to the current directory (and a
  bogus script path for the .exe); it now points at the real file.

### Added
- **Wake-word mishears are editable and learnable.** Config → Wake Phrase has
  an **ALSO ACCEPT** list (`WAKE_WORD_ALTERNATIVES`, previously config.json
  only) and a **SUGGEST FROM HISTORY** button that ranks the short transcripts
  repeatedly discarded as "wake word not found" that sound like your phrase —
  e.g. "hey nova" heard as *noba*, *nova* or *hey noba*. Click to
  accept; nothing is added automatically.
- **Cleanup prompt handles repeats, false starts and self-corrections**
  ("If you… I was… I'm working on" → "I'm working on"; "Tuesday, no,
  Wednesday" → "Wednesday"), fixes unmistakable in-context mishears ("mask
  clicks" → "mouse clicks"), and knows more NibCast / Claude Code sound-alikes.
- `tools/score_history.py` — read-only quality report over your history DB or
  a Dev CSV export: leftover repeats/fillers/false starts, skipped cleanups,
  dictionary near-misses, and log counts (429s, timeouts, wake hit rate).

### Security & privacy
- **Privacy Mode is now actually enforced.** It was saved and displayed but
  never applied: transcripts were still stored in history and written to
  `nibcast.log`. With it on, history keeps counts but no text (including
  overheard wake-word clips), and every transcript-bearing log line is
  replaced by a length placeholder.
- **Debug Bundle scrubs dictated text from the log** it includes, regardless of
  Privacy Mode, so older log lines can't leak into a bug report.
- **Dashboard refuses requests from other sites.** Requests must use a
  localhost Host header (blocks DNS rebinding), and POST/DELETE requests from
  any other browser origin — including other localhost ports — are rejected.
- **Backups no longer include login secrets** (`.vf_*`: PIN/pattern hashes,
  TOTP seed, session key). After restoring a backup on a new machine, set your
  PIN again.
- Dashboard exports (`nibcast_*.csv/pdf`, backup and debug zips) are
  git-ignored so personal history can't be committed by accident.

**Tip:** set Config → AI Backend → `LLM_FALLBACK` to **Ollama** for a fully
local safety net — had it been set, every rate-limited dictation in the
history above would still have been cleaned.

---

## [2.4.2] — 2026-07-16

### Fixed
- **The ambient auto-raise no longer overwrites a threshold you set yourself.**
  Auto-raise judged clips by their loudest 100 ms window while the gate acts on
  a smoothed level near the clip average — so on high-gain mics the raised value
  (peak × 1.02, capped at 0.30) landed *above* the user's actual speaking level
  and the wake word went deaf until the threshold was reset by hand… which the
  next streak overwrote again. Setting the threshold from the dashboard (slider
  or Calibrate) now flips the new `WAKE_AUTO_RAISE_ENABLED` flag off, and the
  engine leaves a hand-set value alone (it logs advice instead). A checkbox
  under the slider turns the automatic behavior back on.
- **Mic-level meter no longer dies permanently on one dropped request** — a
  single failed poll used to stop the meter until the page was reloaded.

### Added
- **Guided threshold calibration.** Config → Wake Word → CALIBRATE now walks
  through two steps — stay quiet (ambient floor), then read a shown sentence at
  normal dictation volume (speech level) — and saves a threshold measured
  between the two. Sampling runs server-side (one request per phase; rapid
  client-side polling proved unreliable inside the pywebview desktop window).
  Refuses with a clear message when voice and background noise are too close
  to separate. Replaces the single-shot peak-based Calibrate.
- **Stale-process banner.** If the code on disk is newer than the running app
  (an update was applied without restarting), the dashboard now shows a
  persistent "quit NibCast and start it again" banner instead of letting new
  page features fail against old routes with cryptic errors.

---

## [2.4.1] — 2026-07-14

### Fixed
- **Wake-word VAD kept recording on loud-ambient setups no matter what threshold
  you set.** The engine clamped the effective wake gate to a hard ceiling of
  `0.08` RMS, while high-gain mics / rooms with background media idle at
  0.10–0.17 — so raising `WAKE_WORD_VAD_THRESHOLD` (dashboard slider or
  config.json) was silently ignored and NibCast recorded and discarded ambient
  clips continuously. The ceiling is now `0.30` (`WAKE_WORD_VAD_THRESHOLD_MAX`
  in `config.py`, single-sourced everywhere), so configured values are actually
  honored; it remains only as a corrupt-config guard.
- **Wake gate now floats above the measured ambient noise floor** (1.5× the
  idle-room level the activator already tracks), so a loud environment stops
  false-triggering automatically even before you touch the threshold.
- **The ambient-streak auto-raise now works on the setups that needed it.** It
  was capped at the same 0.08 ceiling, so it never fired when the configured
  threshold was already above 0.08 — and its log advice suggested values the
  engine would clamp away. It now raises up to the new ceiling, and the warning
  points at the real remaining fix (mute background audio / lower Windows mic
  input level) instead of suggesting ineffective config values.
- **Auto-raise now judges clips by their loudest 100 ms window, not their
  average.** The VAD gate fires on a fast-attack EMA, so background media
  averaging 0.09 RMS can still spike past a 0.19 gate; the old average-based
  bar (`avg > threshold × 0.85`) never cleared on exactly those clips, so the
  raise never happened and the peaks kept triggering. The raise now targets
  just above the observed peak, capped at the ceiling.
- **Dashboard threshold slider capped at 0.15** — below the level ambient sits
  at on the affected setups, and partly a dead zone under the old clamp. Slider
  now goes to 0.30, the mic-level meter is rescaled to match, and saving /
  Calibrate clamp to the same ceiling the engine enforces, so the value you see
  is always the value in effect.
- **Same bug class in the wake silence slider**: the dashboard accepted
  `WAKE_WORD_SILENCE_SEC` down to 0.1 s but the engine silently floored it at
  0.3 s — slider positions 0.1–0.3 did nothing. UI and save clamp now match the
  engine floor.

No config migration needed: existing `config.json` values are unchanged, and
thresholds that were previously being silently clamped simply take effect now.

### Security
- Dashboard session cookie hardened with `SameSite=Lax` + `HttpOnly`, blocking
  cross-site use of an authenticated session (CSRF / DNS-rebinding). The server
  remains bound to `127.0.0.1` only.

### Added
- **Native desktop window + dashboard "flow" polish.** The dashboard now reads
  as a native app: an optional frameless window (`desktop_app.py` / pywebview)
  with custom titlebar controls — minimize / maximize / close — and a draggable
  brand area; plus card depth (drop-shadow + top rim-light), cross-fade panel
  transitions, hover accents, and roomier spacing. The lightweight Chrome
  `--app` launch keeps its own native titlebar. The original dot-grid background
  texture is retained.
- **One-click diagnostics bundle** (sidebar → **Debug Bundle**). Downloads a zip
  with system info, your settings with **every API key redacted**, and the recent
  log — so you can file bug reports without leaking secrets.
- **LLM failover backend** (Config → AI Backend → `LLM_FALLBACK`). When your primary
  LLM is rate-limited (e.g. Groq's free daily token cap → HTTP 429) or errors,
  NibCast retries cleanup with a chosen fallback provider instead of dropping to
  basic cleanup. Opt-in — it never silently switches providers.
- **Smarter Brain Mode selection.** When two engines process the same input,
  NibCast now *scores* each result and keeps the best, instead of a crude
  word-count / filler-count tie-break:
  - **LLM cleanup** — `_score_cleanup()` rewards a complete sentence (terminal
    punctuation) and penalises leftover fillers, chatbot-style hallucinations,
    dropped content, and padding. So if one engine returns a truncated "half a
    sentence", the engine that finished the thought wins.
  - **ASR** — prefers a real transcript over an engine's hallucination
    (e.g. Whisper emitting "Thank you." on a faint clip) before falling back to
    the more-complete (more-words) transcript.
- **Cerebras LLM backend** (Config → AI Backend → LLM). Free, OpenAI-compatible,
  fastest free throughput (Llama 3.3 70B / Qwen3, ~1M tokens/day). The best
  drop-in backup to Groq for transcript cleanup. New keys: `CEREBRAS_API_KEY`,
  `CEREBRAS_LLM_MODEL`. Get a free key at cloud.cerebras.ai.
- **Google Gemini LLM backend** via its OpenAI-compatible endpoint
  (`generativelanguage.googleapis.com/v1beta/openai`). Free Flash tier
  (~1,500 req/day, 1M-token context). New keys: `GEMINI_API_KEY`,
  `GEMINI_LLM_MODEL`. The UI shows a **prominent privacy warning**: Google's free
  tier uses your dictated text to improve its products — use a paid key or a
  different backend for sensitive dictation. (Pro models left the free tier in
  April 2026, so only Flash models are offered.)
- **Model-selection guidance throughout Config → AI Backend.** Each backend and
  model dropdown now carries a plain-language "pick this if unsure / best for
  dictation" hint so new users aren't left guessing.
- **Brain Mode is now genuinely useful for accuracy.** The secondary-engine
  picker recommends **Deepgram nova-3** (a *different* architecture from Whisper)
  as the pairing for Groq Whisper, with inline guidance explaining that picking
  the same engine twice gives no gain. Cross-checking two different engines and
  keeping the better transcript is the main lever for higher accuracy.
- **Persistent "Create Desktop Shortcut" button.** Previously only available on the
  dismissible first-run banner — now also available any time in
  Config → Startup & Display.
- **Writing Style selector** (Config → Output & Cleanup). FLOW / VERBATIM /
  PROFESSIONAL / CONCISE were already implemented in `text_processor.py` but had
  no UI — `WRITING_STYLE` could only be changed by hand-editing `config.json`.
- **Vocabulary Hints** (Config → Output & Cleanup). Exposes `WHISPER_PROMPT` as an
  editable textarea so users can add names/jargon the transcriber keeps
  mis-hearing without touching `config.json`.
- **Advanced Wake Word Timing** (Config → Recording Mode, collapsed by default).
  Exposes `WAKE_WORD_SILENCE_SEC`, `WAKE_WORD_TRIGGER_SEC`,
  `WAKE_WORD_MAX_RECORD_SEC`, and `WAKE_WORD_LISTEN_SEC` as sliders — lets users
  fix a wake phrase that's getting cut off or missed without hand-editing config.
- **`check_pipeline.py`** — static pre-build check for the "instance.method() vs
  module_function()" mismatch that caused the has_configured_backend() crash
  below. `build_exe.py` now runs it before every build and aborts if the same
  pattern reappears. Run it standalone any time with `python check_pipeline.py`.

### Fixed
- **"Wake word works but my command silently disappears."** Phase-2 speaker
  verification was rejecting the *enrolled user's own voice* (scores just under
  the threshold) and discarding the command with **no visible feedback** — the
  spoken text never reached ASR, so nothing showed in status or logs. Now: a
  near-threshold rejection flashes "VOICE NOT RECOGNIZED" with the score and
  saves a `speaker_rejected` history row (far-below scores still discard silently
  so background voices don't ding). Added a **Voice match strictness** slider and
  a **"Require my voice"** toggle to Config → Wake Word so users can loosen or
  disable the check without editing config.json.
- **API-key TEST reported the wrong provider's error.** Each key field's TEST
  tested the *saved active* backend, so adding a Deepgram (or any) key while Groq
  was active produced a misleading "Invalid <wrong-provider> key". The client now
  sends the explicit backend and the server tests *that* one. Added a **Quick add
  — all API keys** panel so every provider key can be pasted and validated in one
  place without switching the active backend.
- **Backend health panel (TEST ALL) omitted Deepgram, Cerebras, and Gemini** —
  their live status was invisible. All three are now probed (Deepgram via its
  `Token` auth scheme).
- **Dashboard toggles `DEEPGRAM_DIARIZE` and `VOICE_ENROLLMENT_ENABLED` weren't
  in `TOGGLE_MAP`**, so their on/off state didn't render correctly. Registered both.
- **False wake-word confirmations from "hey ___" phrases (e.g. "hey Fowler",
  "hey floor" confirming as "hey flow").** For a two-word wake phrase, fuzzy
  rule 3c in `_match_wake_word()` averaged the perfect first-word match
  ("hey"=="hey", Jaro 1.0) in with the content word, letting a weak content word
  (Jaro ~0.76) pass — silently undercutting rule 3b's deliberate 0.80 floor.
  Now, for two-word phrases the *content* word must clear the same 0.80 bar;
  3+ word phrases are unchanged. Verified with a matcher test suite.
- **The wake word could "cancel itself" — the blue listening widget would
  appear after the wake phrase and then immediately disappear, often before
  the red recording widget ever showed.** `_run_release_pipeline()` called
  `transcrib.has_configured_backend()`, but `has_configured_backend()` is a
  module-level function in `transcriber.py`, not a method on `Transcriber` —
  this raised `AttributeError: 'Transcriber' object has no attribute
  'has_configured_backend'` any time an ASR result came back empty (e.g. Whisper
  hallucinating "Okay"/"Thank you" on a faint ambient clip — common during the
  12 s post-wake listening window). The crash was caught by
  `on_hotkey_release()`'s safety wrapper, but that wrapper's recovery path calls
  `_disarm_vad_awake()` and flashes "UNEXPECTED ERROR" on the widget — so any
  stray ambient noise picked up while "awake" would silently disarm the command
  window and flash an error, before the user's actual command was ever heard.
  Because the crash happened before `_asr_unconfigured_warned` could be set,
  this fired on *every* empty/discarded transcript, not just once. Now calls
  the module-level `has_configured_backend()` directly.
- **The LLM cleanup could invent an ending for a clip that was cut off
  mid-sentence.** If `VOICE_VAD_SILENCE_SEC` triggers during a natural
  thinking-pause, the transcript trails off on a conjunction/preposition
  (e.g. "...instead of", "...converting it to a"). The "flow" and
  "professional" styles' instruction to produce "complete sentences" led the
  model to fabricate a plausible-sounding continuation the speaker never said
  (e.g. "...to a" → "...to a text."). All four writing-style prompts now
  explicitly say: if the transcript stops mid-sentence, leave it exactly where
  it ends — don't invent words to finish the thought. As a backstop for models
  that don't follow this, `_is_llm_mode_hallucination()` now also discards
  (falling back to basic cleanup) any output where the raw transcript ends on
  a bare article ("a"/"an"/"the") but the cleaned output doesn't — a sign the
  model named the missing noun itself. A pure word-count check can't catch
  this when the model also drops a filler elsewhere, keeping the total length
  unchanged.
- **Wake-word Phase 2 (the actual command recording) could fail to start almost
  every time on some microphones, even though Phase 1 (the wake word itself)
  was detected reliably.** Command mode required 0.3 s of sustained energy above
  the VAD threshold before recording started — double Phase 1's proven 0.15 s. On
  mics with more amplitude jitter, speech rarely stayed continuously above
  threshold that long, so the blue "listening" widget appeared after the wake
  word but the 12.5 s command window timed out before any recording began,
  looking like the widget "cancelled itself." `VoiceActivator._TRIGGER_SEC` is
  now 0.15 s, matching the Phase-1 default.
- **A short dictated phrase ("can you hear me", "testing one two three") could
  come back as a completely different, unrelated sentence.** Small/fast LLM
  backends sometimes respond to short transcripts as a chat message ("Yes, I can
  hear you!") instead of cleaning them. The existing hallucination guard only
  caught replies starting with a known assistant phrase or more than 3x longer
  than the input — a short conversational reply slipped past both checks.
  `_is_llm_mode_hallucination()` now also discards (falling back to basic
  cleanup) any 2-6 word transcript whose cleaned output shares zero words with
  the original. All four writing-style prompts (flow/verbatim/professional/concise)
  also now explicitly tell the model not to answer or act on transcripts that
  read as questions or commands — they're text to clean, not messages to it.
- **The "Active Target Override" dropdown silently did nothing for 4 of its 5
  options.** Its values (`code`/`docs`/`forms`/`general`) didn't match any category
  `target_manager.py` actually knows about (`terminal`/`vscode`/`browser`/`chat`/
  `email`/`notes`/`generic`), so `detect_target()` ignored the override and fell back
  to auto-detection — the dropdown looked like it took effect but dictation behavior
  never changed. Same mismatch made 4 of the 11 options in the Log panel's category
  filter dead (always zero results). The dead "Transcription Modes" cards (which
  called a `setMode()` that did nothing but a toast) have been replaced with a
  working **Target / Category Override** picker using the real 7 categories, synced
  with the sidebar dropdown; both dropdowns now only list real categories.
  `target_manager.set_override()` also now rejects unknown categories instead of
  silently storing them.
- **An unexpected error mid-pipeline could permanently break both the wake word and
  the recording widget.** `on_hotkey_release()` (used by hotkeys, VAD command
  recordings, and Phase-1 wake detection) had no top-level error handling. Any
  uncaught exception after the widget switched to PROCESSING left it stuck showing
  that state with its elapsed-time counter running forever, and for VAD calls left
  `phase1_busy` permanently `True` — silencing the wake word for the rest of the
  session with no way to recover short of restarting NibCast. The pipeline now runs
  inside a wrapper that resets all wake-word/VAD state, flashes an error on the
  widget, and re-arms the cooldown on any unexpected exception.
- **The wake word gave zero feedback when no ASR backend was configured.** Phase-1
  (wake-word) clips with an empty transcript returned silently, so a fresh install
  that never used a hotkey would see the wake word "never respond" with no
  indication why. It now shows the same one-time "NO API KEY SET" notice as the
  hotkey path.
- **Wake-phrase tester could permanently kill the dashboard.** The dashboard's
  "Test" button for the wake phrase (`/api/test-wake-phrase`) re-imported
  `main.py`, which re-executed the entire entry point as a second module —
  re-running `db.init_db()`, recreating the recorder/transcriber/widgets, and
  tripping the single-instance guard's `sys.exit(0)` *inside the Flask request
  thread*. Because the dev server is single-threaded with no `SystemExit`
  handler, this killed the dashboard for the rest of the process's life. Now
  reads `_match_wake_word` from the already-initialized `__main__` module, and
  `start_dashboard()` survives an uncaught `SystemExit` as defense-in-depth.
- **Fresh installs with no API key failed every dictation with no clear cause.**
  The ASR fallback chain always found a keyless "local" backend and silently
  tried `http://localhost:8000`. A one-time "NO API KEY SET" notification now
  points the user at Config → AI Backend.
- **Microphone open failures were invisible.** If `INPUT_DEVICE` is invalid, the
  mic is denied by Windows privacy settings, or it's in use by another app, NibCast
  now shows a one-time "MIC NOT FOUND" message with the exact settings to check
  instead of leaving the VAD silently dead.
- **Voice enrollment crashed with "MAXIMUM CALL STACK SIZE EXCEEDED."** Encoding a
  recorded sample to base64 via `String.fromCharCode(...bytes)` overflowed the JS
  call stack on real (~220KB) recordings; encoding is now chunked. The enrollment
  error message also no longer blames "microphone access denied" for unrelated
  errors.
- **Desktop icon showed "NibCast is already running" with no way to get to the
  dashboard.** Double-clicking the icon (or Start Menu entry) while NibCast is
  already running now opens the dashboard in the same chromeless app window used
  by the tray/widget, falling back to the dialog only if no browser is available.
- **Dashboard window sometimes opened as a plain browser tab instead of an app
  window.** The "open dashboard" path (tray, widget double-click, desktop-icon
  redirect) is now unified behind one helper that always launches Chrome/Edge in
  chromeless `--app=` mode.

### Planned
- macOS support — requires swapping `pynput`'s Windows-only `_win32` backends,
  handling macOS Accessibility permissions for global hotkeys/text injection,
  reworking the Tkinter overlay's window flags, and packaging as `.app`/`.dmg`
  instead of `.exe`. Not scheduled for a near-term release; tracked as a
  potential future port. Contributions welcome.

---

## [2.4.0] — 2026-06-29

### Added
- **Native desktop window.** Frameless pywebview window (`desktop_app.py`)
  with a custom titlebar (minimize / maximize / close) and a draggable brand
  area; the lightweight Chrome `--app` launch keeps its own native titlebar.
- **One-click scrubbed diagnostics bundle** (sidebar → Debug Bundle): redacted
  config + system info + recent log for bug reports; API keys never included.
- **Opt-in LLM failover backend** (`LLM_FALLBACK_BACKEND`): when the primary
  LLM is rate-limited (429) or errors, retry cleanup with a chosen fallback
  provider instead of dropping to basic cleanup; never silently switches
  providers.

### Changed
- Dashboard depth (drop-shadow + top rim-light), cross-fade panel transitions,
  hover accents, roomier spacing; original dot-grid texture kept.

### Security
- Session cookie hardened (`SameSite=Lax`, `HttpOnly`); server stays on
  127.0.0.1.

---

## [2.3.6] — 2026-06-24

### Fixed
- **Dashboard window now shows the NibCast icon, not a generic globe.** The UI
  opens as a chromeless Chrome/Edge app window (`--app=`), but no favicon was
  served, so the title bar showed the browser's default globe — making it look
  like a web page instead of an app. Added a branded SVG favicon to the login,
  setup, and dashboard pages.
- **Correct PIN-reset path on the login screen.** It told users to delete
  `~/.NIBCAST_local/.vf_auth`; the real file is `~/.nibcast/.vf_auth`, so the
  reset instruction couldn't work. Fixed.

---

## [2.3.5] — 2026-06-22

### Fixed
- **Translate-to-English mode** no longer fails on Groq/OpenAI. It was sending a
  `task=translate` parameter the transcriptions endpoint rejects ("unknown param
  `task`", HTTP 400); it now uses the dedicated `/audio/translations` endpoint.
- **Misleading ERROR logs eliminated.** Capability probes (checking which backends
  *could* serve as a fallback) logged `OPENAI_API_KEY not set` / `CUSTOM_ASR_URL
  not set` / `NVIDIA does not support ASR` at ERROR level even on a healthy
  Groq-only setup. These probes are now quiet; only real failures log as errors.

### Docs
- README updated for the open-source release: custom-dictionary and adaptive
  wake-word features, a link to the new FEATURES.md tour, and accurate
  opt-in/default notes for Brain Mode and Voice Match.
- Added FEATURES.md (full feature tour) and documented the optional `pymupdf`
  dependency for PDF export from source.

---

## [2.3.4] — 2026-06-21

### Changed
- **Speaker verification (voice enrollment) is now OFF by default.** The
  lightweight spectral matcher rejected genuine same-speaker clips often enough
  (scores ~0.50) to make the wake word feel broken on a fresh setup. Out of the
  box the wake word now works for whoever is at the machine; locking it to one
  enrolled voice is opt-in (Config → Wake Word). When enabled, the match
  threshold default is more forgiving (0.62 → 0.50) so the real user isn't
  turned away.

---

## [2.3.3] — 2026-06-21

### Improved
- **Custom dictionary now improves accuracy end-to-end.** The `WHISPER_PROMPT`
  vocabulary (names, jargon, product terms) was only fed to the transcriber and,
  worse, was *silently dropped* once an app had transcript history (the prompt
  used `app_vocab or dictionary`, so learned vocab shadowed the user's curated
  terms). Now the curated dictionary is always combined with learned vocab for
  the transcriber, **and** is passed to the cleanup LLM so it can fix obvious
  mis-hearings of the user's own terms (e.g. "cloud code" → "Claude Code") —
  this is the main gap behind competitors transcribing domain terms correctly.

### Fixed
- **Log spam / hidden errors from Brain Mode.** When the second ASR engine had no
  API key, every single utterance logged an `ERROR` + `WARNING`. Probing an
  optional engine is now quiet, and the "no key" notice is logged once per session
  with clear guidance, instead of burying real errors.

### Changed
- Default Brain-Mode second engine is now **Deepgram** (free credits, strong on
  natural speech) instead of OpenAI. Brain Mode remains **opt-in**: single-engine
  Groq (free tier) is the default and costs nothing; the second engine only runs
  when you turn Brain Mode on, and falls back to primary-only if it has no key.

---

## [2.3.2] — 2026-06-20

### Fixed
- **Dictation no longer cuts off mid-sentence on quiet mics.** After the wake
  word, command (Phase 2) recording used a fixed silence gate
  (`VOICE_VAD_THRESHOLD = 0.030`). On low-gain mics a normal speaking voice sits
  *below* that, so the small gaps between words registered as "done speaking" and
  the turn ended early (e.g. captured only "I'm just checking with the"). The
  command-mode gate is now adaptive: it tracks the mic's ambient noise floor and
  sets the silence threshold to `noise × 2`, bounded below by 0.008 (so true
  silence still ends the turn) and never above the configured value (loud mics
  are unaffected).

---

## [2.3.1] — 2026-06-20

### Fixed
- **Wake word now works across different laptops/mics.** Several
  environment-sensitive failures that made the wake word unreliable (or dead) on
  machines other than the developer's:
  - **Audio input overflow** — the mic stream now uses a fixed block size and
    high-latency buffering, stopping the dropped-audio storm that arrived as
    choppy/truncated wake clips on slower or busier audio stacks.
  - **Wake-word lockout** — a stale `config.json` or the auto-raise heuristic
    could push `WAKE_WORD_VAD_THRESHOLD` above a normal speaking voice (e.g.
    0.15), silently making the wake word impossible to trigger. Added a hard
    runtime ceiling (0.08) and lowered the auto-raise cap to match, so even an
    over-raised config self-heals.
  - **Short-clip mishears** — Phase-1 ASR switched from `whisper-large-v3-turbo`
    to `whisper-large-v3` (markedly more accurate on ~0.6 s clips), and the
    default gate/silence/min-clip values were tuned so quiet mics still register.
- **Enabling voice activation no longer requires a restart.** Toggling the wake
  word in the dashboard now starts/stops the listener live.
- **Crash on launch fixed** (`NameError: name 'wake_enabled' is not defined`) —
  a refactor left an orphaned reference in startup. The build now runs a static
  undefined-name gate (pyflakes) so this class of crash can't ship again.

---

## [2.3.0] — 2026-05-18

### Added
- Wake-word two-phase VAD state machine (Phase 1 sleep → Phase 2 command)
- Voice enrollment and speaker verification (spectral features, no ML dependencies)
- Deepgram Nova-3 ASR backend with optional speaker diarization
- Brain Mode: run two ASR backends in parallel, pick the longer transcript
- Writing style selector: flow / verbatim / professional / concise
- Snippet expansion: map spoken phrases to expanded text
- Whisper transcription hint (`WHISPER_PROMPT`) for domain vocabulary
- Per-hotkey recording modes (hold / toggle / command) stored in `HOTKEY_CONFIGS`
- Raw/clean log toggle in dashboard history panel
- Mic-level calibration meter in dashboard

### Fixed
- Wake recording never starting (Phase-2 VAD was not re-arming after Phase-1 match)
- Widget visibility state lost across state transitions
- Duplicate hotkey entries in `HOTKEY_CONFIGS` after config round-trip

### Changed
- Renamed project from VoiceFlow Local to **NibCast**
- User data directory migrated from `~/.voiceflow_local/` to `~/.nibcast/`
- Auth files migrated from project root to `~/.nibcast/`

---

## [2.2.0] — 2026-04-10

### Added
- Full web dashboard with status, log, insights, modes, and config panels
- Pattern lock (Android-style 3×3 grid) as second auth method
- TOTP / Google Authenticator support
- PDF export (day-grouped, timestamped) — requires `pymupdf`
- Backup export (ZIP of config + history)
- Autostart toggle in dashboard (Windows registry)

### Fixed
- WASAPI dual-stream conflict on Windows — now uses a single persistent InputStream
- Pre-roll buffer restores ~0.47 s of audio lost to VAD onset latency

---

## [2.1.0] — 2026-03-15

### Added
- Always-on-top floating orb widget (3 icon styles: wave, orbit, pulse)
- 3 colour themes: amber, violet, cyan
- System tray icon with right-click menu
- Per-app rules (terminal, VS Code, browser, chat, email, notes)
- Active-window detection via Win32 API

---

## [2.0.0] — 2026-02-01

### Added
- Multi-backend ASR: Groq, OpenAI, local whisper.cpp, custom endpoint
- Multi-backend LLM: Groq, OpenAI, Anthropic Claude, Ollama, NVIDIA NIM, custom
- SQLite transcription history with auto-delete
- CSV export
- Global hotkeys: hold, toggle, and command modes
- PIN authentication for web dashboard

---

## [1.0.0] — 2026-01-01

### Added
- Initial release: single Groq backend, hold-to-talk hotkey, clipboard injection
