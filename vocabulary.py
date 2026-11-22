# ============================================================
#  NibCast — Learned vocabulary (config: VOCAB_AUTO_LEARN)
# ============================================================
#  Builds YOUR word list automatically so the speech engines and
#  the cleanup model get your names, products and jargon right.
#
#  Where words come from
#    • repeat   — names / acronyms / brand spellings you keep saying
#                 (NibCast, GitHub, LLM, CapCut …) across dictations
#    • correct  — words the cleanup had to fix: "new cast" → NibCast
#                 (the misheard form is kept, so the LLM learns it)
#    • edit     — words you changed yourself in the edit-before-paste box
#    • wispr    — a one-click import of your Wispr Flow dictionary
#    • manual   — words you add on the dashboard
#
#  Where words go
#    • Deepgram nova-3 keyterm prompting
#    • the Whisper prompt (Groq / OpenAI / in-app Whisper)
#    • the cleanup LLM's vocabulary hint, with known mishearings
#
#  Guard rails — a wrong word fed to the recogniser makes it hear
#  that word where you never said it, so:
#    • automatic words need several separate dictations (corrections 2)
#    • ordinary English words are never learned automatically
#    • at most VOCAB_MAX_TERMS words are fed, pinned first
#    • a word you remove is blocked for good — it never comes back
#    • automatic words you stop using drop out after VOCAB_FORGET_DAYS
#
#  Everything stays on this computer (history.db).
# ============================================================

from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import threading
import time

import config
from logger import log

# ── Word shapes ───────────────────────────────────────────────

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#'’.\-]*[A-Za-z0-9+#]|[A-Za-z]")
_CAMEL = re.compile(r"[a-z][A-Z]|^[A-Z][a-z]+[A-Z]")          # NibCast, GitHub, iPhone
_ACRONYM = re.compile(r"^[A-Z]{2,6}s?$")                       # API, LLMs, MCP
_ALNUM = re.compile(r"[A-Za-z].*\d|\d.*[A-Za-z]")              # gpt-4o, nova-3, v2
_SENT_BREAK = re.compile(r"(?:[.!?…:;]|\.\.\.|—|–|\"|\(|\n)\s*$")

# Capitalised but ordinary: sentence starters, pronouns, days, months, and the
# words people capitalise for emphasis. Shape tokens (CamelCase / acronyms)
# bypass this list; plain Capitalised words must pass it AND be capitalised
# nearly every time they're used.
_COMMON = frozenset("""
a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing done down during each else even
ever every few for from further get gets getting give go goes going gone got had has have
having he her here hers him his how however i if in into is it its just kind know let like
make makes many may maybe me might mine more most much must my need never next no nor not
now of off ok okay on once one only or other our out over own please really right same see
she should so some something such sure than thank thanks that the their them then there
these they thing things think this those though through to too try under until up us use
very want was way we well were what when where whether which while who why will with
would yeah yes yet you your yours
monday tuesday wednesday thursday friday saturday sunday january february march april june
july august september october november december
hello hi hey dear regards best first second third last new old good great another also
here's there's it's that's what's let's i'm i'll i've i'd you're we're they're don't can't
won't isn't aren't doesn't didn't okay alright actually basically anyway currently
step note update app store play pro code cloud studio clip
""".split())
_DETERMINERS = frozenset({"the", "a", "an", "my", "our", "your", "this", "that", "his", "her", "their"})
_I_FORMS = frozenset({"i", "i'm", "i'll", "i've", "i'd", "ok", "tv"})

MIN_REPEAT_DICTATIONS = 3
MIN_CAPITALISED_DICTATIONS = 4
MIN_CORRECTIONS = 2
CAP_CONSISTENCY = 0.9

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "terms": [], "pairs": [], "strong": []}
_CACHE_SEC = 30


def _db_path() -> str:
    import database
    return database.DB_PATH


def _connect():
    c = sqlite3.connect(_db_path(), check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    _ensure(c)
    return c


def _ensure(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS vocabulary (
            key          TEXT PRIMARY KEY,          -- lower-case term
            term         TEXT NOT NULL,             -- preferred spelling
            source       TEXT NOT NULL DEFAULT 'repeat',
            status       TEXT NOT NULL DEFAULT 'learning',  -- learning|active|pinned|blocked
            dictations   INTEGER NOT NULL DEFAULT 0,
            corrections  INTEGER NOT NULL DEFAULT 0,
            cap_uses     INTEGER NOT NULL DEFAULT 0,
            lower_uses   INTEGER NOT NULL DEFAULT 0,
            misheard     TEXT NOT NULL DEFAULT '[]',  -- JSON list of sound-alikes
            first_seen   REAL NOT NULL DEFAULT 0,
            last_seen    REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS vocabulary_meta (k TEXT PRIMARY KEY, v TEXT);
    """)


def _enabled() -> bool:
    return bool(getattr(config, "VOCAB_AUTO_LEARN", True)) and not getattr(config, "PRIVACY_MODE", False)


def _invalidate():
    _cache["at"] = 0.0


# ── Extraction ────────────────────────────────────────────────

def _is_shape_term(w: str) -> bool:
    return bool(_CAMEL.search(w) or _ACRONYM.match(w) or (_ALNUM.search(w) and len(w) >= 3))


def _tokens_with_position(text: str):
    """Yield (token, starts_sentence, gap_before) for each word, in one pass."""
    text = text or ""
    prev_end = None
    for m in _TOKEN.finditer(text):
        gap = text[prev_end:m.start()] if prev_end is not None else ""
        starts = prev_end is None or bool(_SENT_BREAK.search(gap)) \
            or bool(re.search(r"\n\s*(?:\d+[.)]|[-*•])?\s*$", gap))
        prev_end = m.end()
        yield m.group(0).strip(".'’-"), starts, gap


def candidates_in(text: str) -> dict:
    """{key: (spelling, kind)} for one dictation. kind: 'shape' (CamelCase /
    acronym / alnum), 'cap' (Capitalised word) or 'phrase' (two capitalised
    words in a row: "Claude Code", "Play Store")."""
    out = {}
    prev = None                      # previous token if it was a name-like word
    for w, starts, gap in _tokens_with_position(text):
        k = w.lower()
        name_like = False
        if len(w) >= 2 and k not in _I_FORMS:
            if _is_shape_term(w):
                out.setdefault(k, (w, "shape"))
                name_like = True
            elif w[0].isupper() and w[1:].islower():
                if not starts and k not in _COMMON:
                    out.setdefault(k, (w, "cap"))
                name_like = not starts or k not in _COMMON
        if (name_like and prev and gap.strip() == "" and not starts and w[0].isupper()
                and prev[0].isupper() and prev.lower() not in _DETERMINERS):
            ph = f"{prev} {w}"
            out.setdefault(ph.lower(), (ph, "phrase"))
        prev = w if name_like else None
    return out


def _lowercase_uses(text: str) -> set:
    return {m.group(0).lower() for m in re.finditer(r"\b[a-z][a-z'’\-]+\b", text or "")}


def _sound_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _trim_misheard(words: list, term: str) -> str:
    """Drop neighbouring ordinary words the diff swept in ("the git hub",
    "cap cut or", "read me this") while that makes it sound MORE like the term."""
    while len(words) > 1:
        best = " ".join(words)
        trimmed = None
        for cand in (words[1:], words[:-1]):
            dropped = words[0] if cand == words[1:] else words[-1]
            if dropped.lower() in _COMMON and _sound_ratio(" ".join(cand), term) > _sound_ratio(best, term) + 0.05:
                trimmed = cand
                break
        if trimmed is None:
            break
        words = trimmed
    return " ".join(words)


def corrections_in(raw: str, clean: str) -> list:
    """[(misheard, term)] where the cleanup (or the user) replaced a sound-alike
    with a name / product spelling: "new cast" → "NibCast", "github" → "GitHub"."""
    a = re.findall(r"[A-Za-z0-9'’+#.\-]+", raw or "")
    b = re.findall(r"[A-Za-z0-9'’+#.\-]+", clean or "")
    al = [w.lower().strip(".,") for w in a]
    bl = [w.lower().strip(".,") for w in b]
    out = []
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                rw, cw = a[i].strip(".,"), b[j].strip(".,")
                if rw != cw and rw.lower() == cw.lower() and _is_shape_term(cw):
                    out.append((rw, cw))                       # case fix: github → GitHub
            continue
        if op != "replace" or not (1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 2):
            continue
        new = " ".join(w.strip(".,") for w in b[j1:j2])
        old = _trim_misheard([w.strip(".,") for w in a[i1:i2]], new)
        if old.lower() == new.lower():
            continue
        if not any(_is_shape_term(w.strip(".,")) or (w[:1].isupper() and w.lower().strip(".,") not in _COMMON)
                   for w in b[j1:j2]):
            continue                                          # a grammar fix, not a name
        # must sound alike-ish: share a few letters in order
        if difflib.SequenceMatcher(None, old.lower().replace(" ", ""), new.lower().replace(" ", "")).ratio() < 0.5:
            continue
        out.append((old, new))
    return out


# ── Recording ─────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s'’.\-]+", "", (s or "").lower())


def _useful_misheard(misheard: str, term: str) -> bool:
    """Is "misheard → term" worth teaching the LLM? Not for plurals/possessives
    ("githubs", "api's"), not for ordinary phrases ("you I" → UI would rewrite
    real speech), and not for long fragments."""
    m = (misheard or "").strip()
    if not m or len(m.split()) > 3:
        return False
    nm, nt = _norm(m), _norm(term)
    if nm == nt or nm.rstrip("s") == nt.rstrip("s"):
        return False
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", m)]
    if words and all(w in _COMMON for w in words):
        return False
    # A single ordinary-looking word ("cast", "open") is too risky to rewrite
    # unless it's nearly the whole term.
    if len(words) == 1 and m.islower() and len(nm) < 0.75 * len(nt):
        return False
    return True


def _upsert(c, term: str, source: str, *, dictation=False, correction=False,
            misheard: str = "", cap=0, low=0, status: str | None = None, now=None):
    now = now or time.time()
    k = term.lower()
    row = c.execute("SELECT * FROM vocabulary WHERE key=?", (k,)).fetchone()
    if row is None:
        c.execute("""INSERT INTO vocabulary(key, term, source, status, first_seen, last_seen)
                     VALUES (?,?,?,?,?,?)""", (k, term, source, status or "learning", now, now))
        row = c.execute("SELECT * FROM vocabulary WHERE key=?", (k,)).fetchone()
    if row["status"] == "blocked" and status not in ("active", "pinned"):
        return
    mis = json.loads(row["misheard"] or "[]")
    if misheard and _useful_misheard(misheard, term) and misheard.lower() not in [m.lower() for m in mis]:
        mis = (mis + [misheard])[-6:]
    # The spelling seen most recently in a correction / edit / manual entry wins;
    # a plain repeat never overrides a deliberate spelling.
    spelling = term if source in ("correct", "edit", "manual", "wispr") or row["source"] == "repeat" else row["term"]
    src = row["source"]
    rank = {"repeat": 0, "correct": 1, "edit": 2, "wispr": 3, "manual": 4}
    if rank.get(source, 0) > rank.get(src, 0):
        src = source
    c.execute("""UPDATE vocabulary SET term=?, source=?, dictations=dictations+?, corrections=corrections+?,
                 cap_uses=cap_uses+?, lower_uses=lower_uses+?, misheard=?, last_seen=?,
                 status=COALESCE(?, status) WHERE key=?""",
              (spelling, src, int(dictation), int(correction), cap, low, json.dumps(mis), now,
               status, k))


def _promote(c, now=None):
    """learning → active once the evidence is there."""
    c.execute(f"""UPDATE vocabulary SET status='active'
                  WHERE status='learning' AND (
                    corrections >= {MIN_CORRECTIONS}
                    OR source IN ('edit','wispr','manual')
                    OR (dictations >= {MIN_REPEAT_DICTATIONS} AND (term GLOB '*[A-Z]*[A-Z]*' OR term GLOB '*[0-9]*')
                        AND cap_uses >= {CAP_CONSISTENCY} * (cap_uses + lower_uses))
                    OR (dictations >= {MIN_CAPITALISED_DICTATIONS}
                        AND cap_uses >= {CAP_CONSISTENCY} * (cap_uses + lower_uses)))""")
    # A word you've started writing in lower case is just a word — demote it.
    c.execute(f"""UPDATE vocabulary SET status='learning'
                  WHERE status='active' AND source='repeat'
                    AND NOT (term GLOB '*[A-Z]*[A-Z]*' OR term GLOB '*[0-9]*')
                    AND cap_uses < {CAP_CONSISTENCY} * (cap_uses + lower_uses)""")


def _learn_into(c, raw: str, clean: str, now=None):
    cands = candidates_in(clean)
    lows = _lowercase_uses(clean)
    for k, (w, kind) in cands.items():
        _upsert(c, w, "repeat", dictation=True, cap=1, now=now)
    # lower-case uses of known capitalised words count against them
    if lows:
        known = {r["key"] for r in c.execute(
            "SELECT key FROM vocabulary WHERE key IN (%s)" % ",".join("?" * len(lows)), list(lows))}
        for k in known - set(cands):
            c.execute("UPDATE vocabulary SET lower_uses=lower_uses+1 WHERE key=?", (k,))
    for old, new in corrections_in(raw, clean):
        _upsert(c, new, "correct", correction=True, misheard=old, now=now)


def learn(raw: str, clean: str):
    """Call after every successful dictation. Never raises."""
    if not _enabled() or not clean:
        return
    try:
        with _lock, _connect() as c:
            _learn_into(c, raw, clean)
            _promote(c)
        _invalidate()
    except Exception as e:
        log.debug(f"vocabulary learn skipped: {e}")


def learn_from_edit(before: str, after: str):
    """The user changed the text in the edit-before-paste box — the strongest
    signal there is: their spelling is right by definition."""
    if not _enabled() or not after or before == after:
        return
    try:
        with _lock, _connect() as c:
            for old, new in corrections_in(before, after):
                _upsert(c, new, "edit", correction=True, misheard=old)
            _promote(c)
        _invalidate()
    except Exception as e:
        log.debug(f"vocabulary edit-learn skipped: {e}")


def backfill(force: bool = False) -> int:
    """Learn from the dictation history once (first run), so the list is useful
    from day one. Returns the number of dictations read."""
    if not _enabled() and not force:
        return 0
    try:
        with _lock, _connect() as c:
            done = c.execute("SELECT v FROM vocabulary_meta WHERE k='backfilled'").fetchone()
            if done and not force:
                return 0
            if force:
                c.execute("DELETE FROM vocabulary WHERE status IN ('learning','active') AND source IN ('repeat','correct')")
            rows = c.execute("""SELECT raw_text, clean_text, created_at FROM transcriptions
                                WHERE status='success' AND clean_text != '' ORDER BY id""").fetchall()
            for r in rows:
                try:
                    ts = time.mktime(time.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    ts = None
                _learn_into(c, r["raw_text"] or "", r["clean_text"] or "", now=ts)
            _promote(c)
            c.execute("INSERT OR REPLACE INTO vocabulary_meta(k, v) VALUES ('backfilled', ?)", (str(time.time()),))
        _invalidate()
        n_active = len(active_terms())
        log.info(f"📚 Learned vocabulary from {len(rows)} past dictations — {n_active} word(s) in use")
        return len(rows)
    except sqlite3.OperationalError as e:
        log.debug(f"vocabulary backfill skipped: {e}")
        return 0


def backfill_async():
    threading.Thread(target=backfill, daemon=True, name="VocabBackfill").start()


# ── Manual control ────────────────────────────────────────────

def add(term: str, misheard: str = "") -> bool:
    term = (term or "").strip()
    if not term or len(term) > 40 or re.search(r"https?:|www\.", term):
        return False
    with _lock, _connect() as c:
        _upsert(c, term, "manual", misheard=misheard.strip(), status="active")
    _invalidate()
    return True


def set_status(term: str, status: str) -> bool:
    """pin / unpin (→ active) / block (remove for good) / unblock (→ active)."""
    if status not in ("pinned", "active", "blocked"):
        return False
    with _lock, _connect() as c:
        n = c.execute("UPDATE vocabulary SET status=? WHERE key=?", (status, term.strip().lower())).rowcount
    _invalidate()
    return bool(n)


def import_wispr(path: str | None = None) -> int:
    """Copy the words from Wispr Flow's personal dictionary (read-only)."""
    path = path or os.path.join(os.environ.get("APPDATA", ""), "Wispr Flow", "flow.sqlite")
    if not os.path.exists(path):
        raise FileNotFoundError("Wispr Flow's database wasn't found on this computer")
    import pathlib
    uri = pathlib.Path(path).resolve().as_uri()      # file:///C:/…/Wispr%20Flow/flow.sqlite
    try:
        src = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=5)
        src.execute("SELECT 1 FROM Dictionary LIMIT 1")
    except sqlite3.Error:
        # Wispr Flow holds the file open; immutable mode reads it without
        # touching its lock/WAL files (may miss words added in the last minutes).
        src = sqlite3.connect(f"{uri}?immutable=1", uri=True, timeout=5)
    try:
        cols = {r[1] for r in src.execute("PRAGMA table_info(Dictionary)")}
        where = "WHERE COALESCE(isDeleted,0)=0" if "isDeleted" in cols else ""
        snip = "AND COALESCE(isSnippet,0)=0" if "isSnippet" in cols and where else ""
        rows = src.execute(f"SELECT phrase, replacement FROM Dictionary {where} {snip}").fetchall()
    finally:
        src.close()
    n = 0
    with _lock, _connect() as c:
        for phrase, repl in rows:
            phrase, repl = (phrase or "").strip(), (repl or "").strip()
            term = repl or phrase
            # Vocabulary only: skip expansions ("btw" → "by the way"), anything
            # with an e-mail address or link (it would be sent to the speech
            # service with every dictation), and long entries.
            if (not term or len(term) > 40 or len(term.split()) > 3
                    or re.search(r"@|https?:|www\.", term + " " + phrase)
                    or (repl and len(repl.split()) > len(phrase.split()))):
                continue
            _upsert(c, term, "wispr", misheard=phrase if repl else "", status=None)
            n += 1
        _promote(c)
    _invalidate()
    log.info(f"📚 Imported {n} word(s) from Wispr Flow's dictionary")
    return n


# ── Reading ───────────────────────────────────────────────────

def list_all() -> list:
    with _lock, _connect() as c:
        rows = c.execute("""SELECT * FROM vocabulary WHERE status != 'learning'
                            ORDER BY CASE status WHEN 'pinned' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                                     corrections*3 + dictations DESC, term""").fetchall()
    used = {t.lower() for t in active_terms()}
    return [{"term": r["term"], "source": r["source"], "status": r["status"],
             "dictations": r["dictations"], "corrections": r["corrections"],
             "misheard": json.loads(r["misheard"] or "[]"), "in_use": r["key"] in used}
            for r in rows]


def _load():
    now = time.time()
    if now - _cache["at"] < _CACHE_SEC:
        return
    limit = max(0, int(getattr(config, "VOCAB_MAX_TERMS", 50)))
    forget = float(getattr(config, "VOCAB_FORGET_DAYS", 90)) * 86400
    terms, pairs, strong = [], [], []
    try:
        with _lock, _connect() as c:
            rows = c.execute("""SELECT term, status, source, misheard, last_seen FROM vocabulary
                                WHERE status IN ('pinned','active')
                                ORDER BY CASE status WHEN 'pinned' THEN 0 ELSE 1 END,
                                         CASE WHEN source IN ('manual','wispr','edit') THEN 0 ELSE 1 END,
                                         corrections*3 + dictations DESC, last_seen DESC""").fetchall()
        for r in rows:
            # Only words learned purely from repetition fade; corrected, imported,
            # edited, manual and pinned words stay until you remove them.
            fades = r["status"] == "active" and r["source"] == "repeat"
            if fades and forget and now - (r["last_seen"] or 0) > forget:
                continue
            if len(terms) < limit:
                terms.append(r["term"])
                strong.append(r["status"] == "pinned" or r["source"] in ("manual", "wispr", "edit"))
                for m in json.loads(r["misheard"] or "[]"):
                    pairs.append((m, r["term"]))
    except Exception as e:
        log.debug(f"vocabulary load skipped: {e}")
    _cache.update(at=now, terms=terms, pairs=pairs[:40], strong=strong)


def keyterms() -> list:
    """Learned words safe to boost in Deepgram: very short acronyms (AI, ID, PC)
    boosted as keyterms get "heard" in ordinary speech, so only words you
    pinned or added yourself may be that short."""
    _load()
    return [t for t, strong in zip(_cache["terms"], _cache["strong"]) if strong or len(t) >= 3]


def active_terms() -> list:
    """Words to feed the recognisers and the LLM, best first."""
    if not getattr(config, "VOCAB_AUTO_LEARN", True):
        return [t for t in _manual_only()]
    _load()
    return list(_cache["terms"])


def _manual_only() -> list:
    try:
        with _lock, _connect() as c:
            return [r["term"] for r in c.execute(
                "SELECT term FROM vocabulary WHERE status IN ('pinned','active') AND source IN ('manual','wispr','edit')")]
    except Exception:
        return []


def known_mishearings() -> list:
    """[(misheard, term)] for the cleanup LLM."""
    _load()
    return list(_cache["pairs"])


def whisper_prompt(extra_terms=(), user_terms=()) -> str:
    """A short, punctuated sentence naming the terms. Whisper copies its prompt's
    style, so a capitalised, punctuated sentence nudges punctuated output, while a
    bare list of lower-case words (the old per-app hint) produced lower-case,
    unpunctuated run-ons."""
    def dedup(items, seen):
        out = []
        for t in items:
            t = (t or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out
    seen: set = set()
    # Kept short: Whisper copies its prompt's style, and a long comma list
    # starts to show up as commas between ordinary words.
    user = dedup(user_terms, seen)[-16:]
    extra = dedup(extra_terms, seen)[:4]
    room = max(0, 24 - len(user) - len(extra))
    learned = dedup(active_terms(), seen)[:room]
    terms = extra + learned + user          # user's own list last = survives truncation
    if not terms:
        return ""
    body = ", ".join(terms[:-1]) + (" and " if len(terms) > 1 else "") + terms[-1]
    return f"This dictation may mention {body}."[-800:]
