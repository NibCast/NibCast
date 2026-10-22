# ============================================================
#  NibCast — History Quality Report (read-only)
# ============================================================
#  Scores every past dictation in ~/.nibcast/history.db and
#  summarises ~/.nibcast/nibcast.log, so cleanup and wake-word
#  problems can be measured instead of guessed.
#
#  Usage:
#     python tools/score_history.py                 # summary + 15 worst entries
#     python tools/score_history.py --worst 40
#     python tools/score_history.py --csv report.csv
#     python tools/score_history.py --db PATH --log PATH
#     python tools/score_history.py --from-csv nibcast_dev_YYYYMMDD.csv
#         (the Insights → Export → Dev CSV file, instead of the live DB)
#
#  Never writes to the database. Stdlib only — runs from the
#  source tree or any Python 3.10+ without NibCast's deps.
# ============================================================

import argparse
import csv
import difflib
import json
import os
import re
import sqlite3
import sys
from collections import Counter

USER_DIR = os.path.join(os.path.expanduser("~"), ".nibcast")

_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Doubled words that are usually legitimate English ("had had", "that that").
_OK_DOUBLES = {"had", "that", "is", "do", "very", "really", "so", "no", "bye", "ha", "go"}

_FILLER_RE = re.compile(
    r"\b(uh+|um+|erm|hmm+|you know|i mean|basically|literally|kind of like|sort of like)\b",
    re.IGNORECASE,
)

# False start: a short fragment abandoned with "..." or "—" before a restart.
_FALSE_START_RE = re.compile(r"\b\w+(?:\s+\w+){0,3}\s*(?:\.\.\.|…|—)\s+", re.IGNORECASE)

# Openers that text_processor._is_llm_mode_hallucination() treats as "the LLM
# answered like a chatbot". If the SPEAKER's own words start this way, the
# guard throws away a correct cleanup and pastes basic cleanup instead.
_GUARD_OPENERS = (
    "here is", "here's", "here are the", "based on", "it seems like", "it looks like",
    "sure,", "sure!", "of course!", "certainly!", "absolutely,", "great!",
    "i understand", "i cannot", "i can't", "unfortunately,", "to answer",
    "in response", "as requested", "i'm ready", "i am ready", "how can i",
    "what would you", "i can help", "i'll help", "let me help",
)


def _words(s: str) -> list:
    return [w.lower() for w in _WORD_RE.findall(s or "")]


def doubled_words(text: str) -> list:
    w = _words(text)
    return [a for a, b in zip(w, w[1:]) if a == b and a not in _OK_DOUBLES]


def repeated_phrases(text: str, max_n: int = 5) -> list:
    """Adjacent repeated 2–5 word phrases ("give me the give me the")."""
    w = _words(text)
    hits = []
    for n in range(max_n, 1, -1):
        i = 0
        while i + 2 * n <= len(w):
            if w[i:i + n] == w[i + n:i + 2 * n]:
                hits.append(" ".join(w[i:i + n]))
                i += 2 * n
            else:
                i += 1
    return hits


def fidelity(raw: str, clean: str):
    """Same Jaccard word-overlap the dashboard's SHOW SCORE badge uses."""
    rs, cs = set(_words(raw)), set(_words(clean))
    if not rs or not cs:
        return None
    return round(100 * len(rs & cs) / len(rs | cs))


def looks_like_basic_cleanup(raw: str, clean: str) -> bool:
    """True when the LLM evidently didn't run (or its output was discarded):
    the pasted text is just the raw transcript, capitalised, with a period."""
    r = (raw or "").strip()
    c = (clean or "").strip()
    if not r or not c:
        return False
    basic = r[0].upper() + r[1:]
    if basic[-1] not in ".!?":
        basic += "."
    return c in (r, basic)


def guard_would_misfire(raw: str) -> bool:
    r = (raw or "").strip().lower()
    return any(r.startswith(p) for p in _GUARD_OPENERS)


def load_vocab(config_path: str) -> list:
    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            prompt = (json.load(f) or {}).get("WHISPER_PROMPT", "")
    except Exception:
        return []
    return [t.strip() for t in prompt.split(",") if t.strip()]


def vocab_misses(text: str, vocab: list) -> list:
    """Words / word pairs that look like a dictionary term but aren't spelled
    like it ("Nipcache" ≈ "NibCast"). Compared with spaces removed so
    "nib cast" and "nipcast" both line up with "NibCast"."""
    if not vocab:
        return []
    w = _WORD_RE.findall(text or "")
    misses = []
    for term in vocab:
        key = re.sub(r"\s+", "", term).lower()
        if len(key) < 5:
            continue
        # A pair that already contains the correctly spelled term ("the NibCast")
        # isn't a miss — only consider pairs where neither half is the term.
        cands = {x.lower(): x for x in w}
        cands.update({(a + b).lower(): f"{a} {b}" for a, b in zip(w, w[1:])
                      if a.lower() != key and b.lower() != key})
        for g, shown in cands.items():
            if g == key or abs(len(g) - len(key)) > 3:
                continue
            if difflib.SequenceMatcher(None, g, key).ratio() >= 0.75:
                misses.append(f"{shown}→{term}")
    return misses


def score_entry(raw: str, clean: str, vocab: list) -> dict:
    d = doubled_words(clean)
    p = repeated_phrases(clean)
    f = _FILLER_RE.findall(clean or "")
    fs = _FALSE_START_RE.findall(clean or "")
    v = vocab_misses(clean, vocab)
    basic = looks_like_basic_cleanup(raw, clean)
    guard = guard_would_misfire(raw)
    nwords = max(1, len(_words(clean)))
    # 100 = nothing left to fix. Penalties are per 100 words so long
    # dictations aren't punished just for being long.
    per100 = 100.0 / nwords
    score = 100.0
    score -= 6 * len(d) * per100
    score -= 8 * len(p) * per100
    score -= 4 * len(f) * per100
    score -= 5 * len(fs) * per100
    score -= 10 * len(v)
    if basic and nwords > 4:
        score -= 25
    return {
        "score": max(0, round(score)),
        "fidelity": fidelity(raw, clean),
        "doubled": d, "phrases": p, "fillers": f, "false_starts": fs,
        "vocab_misses": v, "basic_cleanup": basic, "guard_misfire": guard,
        "words": nwords,
    }


# ── Log summary ───────────────────────────────────────────────

_LOG_PATTERNS = {
    "vad_triggers":        "VAD: voice detected",
    "wake_confirmed":      "Wake word confirmed",
    "wake_via_alternative": "Wake word matched via alternative",
    "wake_not_found":      "Wake word not found",
    "wake_clip_too_short": "Wake L1: clip too short",
    "wake_clip_capped":    "Wake L1b: clip hit",
    "asr_hallucination":   "ASR hallucination",
    "speaker_rejected":    "speaker verify: rejected",
    "llm_guard_discard":   "responded as assistant",
    "llm_stream_guard":    "streamed assistant response",
    "llm_empty_fallback":  "LLM returned empty",
    "llm_failover":        "falling back to [",
    "http_429":            "429",
    "llm_timeout":         "LLM timeout",
    "hotkey_restarts":     "HotkeyThread died",
    "near_silence":        "near-silence",
}


def summarise_log(path: str) -> Counter:
    c = Counter()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for k, needle in _LOG_PATTERNS.items():
                    if needle in line:
                        c[k] += 1
    except FileNotFoundError:
        pass
    return c


def load_db(path: str) -> list:
    # Read-only URI so the running app's DB can't be touched.
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    cols = {r[1] for r in db.execute("PRAGMA table_info(transcriptions)").fetchall()}
    extra = ", llm_status" if "llm_status" in cols else ""   # schema v3+ (NibCast 2.4.3)
    rows = [dict(r) for r in db.execute(
        "SELECT id, created_at, raw_text, clean_text, status, target_app" + extra +
        " FROM transcriptions ORDER BY id"
    ).fetchall()]
    db.close()
    return rows


# Column headers written by database.export_csv(mode="dev").
_DEV_CSV_COLS = {"#": "id", "Timestamp": "created_at", "Application": "target_app",
                 "Status": "status", "Transcription (Clean)": "clean_text",
                 "Raw Speech": "raw_text", "LLM Status": "llm_status"}


def load_dev_csv(path: str) -> list:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [{v: r.get(k, "") for k, v in _DEV_CSV_COLS.items()} for r in csv.DictReader(f)]
    return sorted(rows, key=lambda r: r["created_at"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score NibCast dictation history (read-only).")
    ap.add_argument("--db", default=os.path.join(USER_DIR, "history.db"))
    ap.add_argument("--log", default=os.path.join(USER_DIR, "nibcast.log"))
    ap.add_argument("--config", default=os.path.join(USER_DIR, "config.json"))
    ap.add_argument("--worst", type=int, default=15, help="how many low-scoring entries to print")
    ap.add_argument("--csv", default="", help="write every scored entry to this CSV file")
    ap.add_argument("--from-csv", default="", help="score a dashboard Dev CSV export instead of the DB")
    args = ap.parse_args(argv)

    vocab = load_vocab(args.config)
    if args.from_csv:
        rows = load_dev_csv(args.from_csv)
        source = args.from_csv
    else:
        if not os.path.exists(args.db):
            print(f"No history database at {args.db}")
            return 1
        rows = load_db(args.db)
        source = args.db

    statuses = Counter(r["status"] for r in rows)
    scored = []
    for r in rows:
        if r["status"] != "success" or not (r["clean_text"] or "").strip():
            continue
        s = score_entry(r["raw_text"] or "", r["clean_text"], vocab)
        # 2.4.3+ records how each entry was cleaned — trust it over the heuristic.
        if r.get("llm_status"):
            s["basic_cleanup"] = r["llm_status"].startswith("basic:")
        s.update(id=r["id"], created_at=r["created_at"], app=r["target_app"],
                 raw=r["raw_text"] or "", clean=r["clean_text"])
        scored.append(s)

    n = len(scored)
    print(f"\nNibCast history report — {source}")
    print(f"  rows: {len(rows)}   by status: {dict(statuses)}")
    if not n:
        print("  (no successful dictations with text — Privacy Mode may be on)")
    else:
        avg = sum(s["score"] for s in scored) / n
        fids = [s["fidelity"] for s in scored if s["fidelity"] is not None]
        pct = lambda k: 100 * sum(1 for s in scored if s[k]) / n
        print(f"  scored dictations:           {n}")
        print(f"  average cleanup score:       {avg:.1f} / 100")
        if fids:
            print(f"  average fidelity (dashboard): {sum(fids) / len(fids):.1f}")
        print(f"  doubled words left in:       {pct('doubled'):.1f}% of entries")
        print(f"  repeated phrases left in:    {pct('phrases'):.1f}%")
        print(f"  fillers left in:             {pct('fillers'):.1f}%")
        print(f"  false starts left in:        {pct('false_starts'):.1f}%")
        print(f"  dictionary near-misses:      {pct('vocab_misses'):.1f}%"
              + ("" if vocab else "   (no WHISPER_PROMPT vocabulary found)"))
        print(f"  looks like LLM was skipped:  {pct('basic_cleanup'):.1f}%")
        print(f"  raw starts with a guard opener (cleanup likely discarded): "
              f"{pct('guard_misfire'):.1f}%")
        why = Counter(r["llm_status"] for r in rows
                      if r.get("status") == "success" and r.get("llm_status"))
        if why:
            print("  how entries were cleaned (2.4.3+): "
                  + ", ".join(f"{k}={v}" for k, v in why.most_common()))

        miss = Counter(m for s in scored for m in s["vocab_misses"])
        if miss:
            print("\n  most common dictionary near-misses:")
            for m, c in miss.most_common(10):
                print(f"    {c:4d}  {m}")

        print(f"\n  {min(args.worst, n)} lowest-scoring entries:")
        for s in sorted(scored, key=lambda x: x["score"])[:args.worst]:
            flags = [k for k in ("doubled", "phrases", "fillers", "false_starts",
                                 "vocab_misses") if s[k]]
            if s["basic_cleanup"]:
                flags.append("basic_cleanup")
            print(f"\n  #{s['id']}  {s['created_at']}  score={s['score']}  "
                  f"app={s['app']}  [{', '.join(flags)}]")
            print(f"    raw:   {s['raw'][:220]}")
            print(f"    clean: {s['clean'][:220]}")

    logc = summarise_log(args.log)
    if logc:
        print(f"\nLog summary — {args.log}")
        for k in _LOG_PATTERNS:
            if logc[k]:
                print(f"  {k:22s} {logc[k]}")
        trig = logc["vad_triggers"]
        conf = logc["wake_confirmed"]
        if trig:
            print(f"  → {conf} wake confirmations from {trig} VAD triggers "
                  f"({100 * conf / trig:.1f}%); every other trigger was a cloud "
                  f"ASR call that found no wake phrase")

    if args.csv and scored:
        cols = ["id", "created_at", "app", "score", "fidelity", "words", "basic_cleanup",
                "guard_misfire", "doubled", "phrases", "fillers", "false_starts",
                "vocab_misses", "raw", "clean"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for s in scored:
                w.writerow(["; ".join(s[c]) if isinstance(s[c], list) else s[c] for c in cols])
        print(f"\nWrote {len(scored)} rows → {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
