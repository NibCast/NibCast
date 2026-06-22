# ============================================================
#  NibCast — Database (SQLite History)
# ============================================================
import os
import re
import sqlite3
import csv
import io
from collections import Counter

from config import USER_DIR
from logger import log

DB_PATH = os.path.join(USER_DIR, "history.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # allows concurrent reads during writes
    return c


SCHEMA_VERSION = 2


def _get_user_version(db) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def _set_user_version(db, v: int):
    db.execute(f"PRAGMA user_version = {int(v)}")


def _migrate(db):
    v = _get_user_version(db)
    if v < 1:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS transcriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
                raw_text    TEXT NOT NULL DEFAULT '',
                clean_text  TEXT NOT NULL DEFAULT '',
                duration_sec REAL DEFAULT 0,
                word_count  INTEGER DEFAULT 0,
                char_count  INTEGER DEFAULT 0,
                target_app  TEXT DEFAULT 'Unknown',
                category    TEXT DEFAULT 'generic',
                language    TEXT DEFAULT 'en',
                status      TEXT DEFAULT 'success'
            );
            CREATE INDEX IF NOT EXISTS idx_created ON transcriptions(created_at);
            CREATE INDEX IF NOT EXISTS idx_target  ON transcriptions(target_app);
        """)
        _set_user_version(db, 1)
    if v < 2:
        # Add optional 'error' column for failed attempts.
        # Only bump version when the column is confirmed present so a disk-full or
        # permissions error doesn't silently mark the migration as done.
        col_exists = any(
            row[1] == "error"
            for row in db.execute("PRAGMA table_info(transcriptions)").fetchall()
        )
        if not col_exists:
            db.execute("ALTER TABLE transcriptions ADD COLUMN error TEXT DEFAULT ''")
        _set_user_version(db, 2)


def init_db():
    with _conn() as db:
        _migrate(db)
    log.info(f"✅ Database ready at {DB_PATH} (schema v{SCHEMA_VERSION})")


def save_transcription(raw_text: str, clean_text: str, duration_sec: float = 0.0,
                       target_app: str = "Unknown", category: str = "generic",
                       language: str = "en", status: str = "success",
                       error: str = ""):
    wc = len(clean_text.split()) if clean_text else 0
    cc = len(clean_text)
    with _conn() as db:
        db.execute(
            """INSERT INTO transcriptions
               (raw_text, clean_text, duration_sec, word_count, char_count,
                target_app, category, language, status, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (raw_text, clean_text, duration_sec, wc, cc, target_app, category,
             language, status, error)
        )
    log.info(f"💾 Saved: {wc} words | {target_app}")
    if status == "success":
        try:
            import state as _st
            _st.add_session_usage(wc, duration_sec)
        except Exception:
            pass


def delete_transcription(tid: int):
    with _conn() as db:
        db.execute("DELETE FROM transcriptions WHERE id = ?", (tid,))


def clear_all_history():
    with _conn() as db:
        db.execute("DELETE FROM transcriptions")
    log.info("All history cleared")


def delete_older_than(days: int) -> int:
    """Delete transcriptions older than `days` days. Returns count deleted."""
    with _conn() as db:
        cur = db.execute(
            "DELETE FROM transcriptions WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        deleted = cur.rowcount
    if deleted:
        log.info(f"Auto-deleted {deleted} transcriptions older than {days} days")
    return deleted


def get_history(limit: int = 200, search: str = "", category: str = ""):
    q = "SELECT * FROM transcriptions WHERE status='success' AND clean_text != ''"
    args = []
    if search:
        q += " AND (clean_text LIKE ? OR raw_text LIKE ? OR target_app LIKE ?)"
        args += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if category and category != "all":
        q += " AND category = ?"
        args.append(category)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)

    with _conn() as db:
        rows = db.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def export_csv(mode: str = "user") -> str:
    """Export transcription history as CSV.

    mode='user'  — clean columns for everyday use (no internal fields)
    mode='dev'   — all columns including raw text, status, error, char count, ID
    """
    rows = get_history(limit=10_000) if mode != "dev" else _get_all_rows(limit=10_000)
    if not rows:
        return ""

    if mode == "dev":
        _FIELDS = [
            ("id",           "#"),
            ("created_at",   "Timestamp"),
            ("target_app",   "Application"),
            ("category",     "Category"),
            ("language",     "Language"),
            ("status",       "Status"),
            ("clean_text",   "Transcription (Clean)"),
            ("raw_text",     "Raw Speech"),
            ("llm_changed",  "LLM Cleaned"),
            ("word_count",   "Words"),
            ("char_count",   "Chars"),
            ("duration_sec", "Duration (s)"),
            ("error",        "Error"),
        ]
    else:
        _FIELDS = [
            ("created_at",   "Date & Time"),
            ("target_app",   "Application"),
            ("category",     "Category"),
            ("clean_text",   "Transcription"),
            ("raw_text",     "Raw Speech"),
            ("word_count",   "Words"),
            ("duration_sec", "Duration (s)"),
            ("language",     "Language"),
        ]

    col_keys    = [f[0] for f in _FIELDS]
    col_headers = [f[1] for f in _FIELDS]

    # UTF-8 BOM so Excel opens accented / non-Latin characters correctly
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(col_headers)
    for r in rows:
        row_out = []
        for k in col_keys:
            if k == "llm_changed":
                raw  = (r.get("raw_text")   or "").strip()
                cln  = (r.get("clean_text") or "").strip()
                row_out.append("Yes" if raw != cln else "No")
            else:
                row_out.append(r.get(k, ""))
        writer.writerow(row_out)
    return buf.getvalue()


def _get_all_rows(limit: int = 10_000) -> list:
    """Like get_history but returns ALL statuses — used for the developer CSV export."""
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM transcriptions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "is", "it", "i", "you", "we", "they", "was", "are", "be", "do",
    "this", "that", "with", "have", "has", "had", "will", "would", "can",
    "could", "should", "may", "might", "not", "no", "yes", "so", "as",
    "if", "then", "else", "get", "set", "go", "make", "take", "use",
    "just", "also", "there", "been", "from", "by", "about", "its", "my",
    "me", "him", "her", "our", "your", "their", "all", "one", "two",
    "new", "now", "when", "than", "more", "into", "some", "what",
})


def get_app_vocabulary(target_label: str, max_terms: int = 50) -> str:
    """Return a space-separated vocabulary hint for Whisper built from past
    transcriptions for this target app.  High-frequency domain words (names,
    technical terms, acronyms) that appear in ≥2 sessions are included so
    Whisper already knows the vocabulary it's likely to hear.
    Returns empty string when there's not enough history yet.

    IMPORTANT: terms are joined with SPACES, not commas. Whisper mimics the
    punctuation style of its initial_prompt — a comma-separated list makes it
    emit a comma after almost every word ("this, is, a, test, ..."). A plain
    space-separated word list primes the same vocabulary without that artifact."""
    if not target_label:
        return ""
    with _conn() as db:
        rows = db.execute(
            """SELECT clean_text FROM transcriptions
               WHERE target_app LIKE ? AND status='success' AND clean_text != ''
               ORDER BY created_at DESC LIMIT 120""",
            (f"%{target_label.split()[-1]}%",),   # match on last word of label (e.g. "Terminal" from "🖥️ Terminal")
        ).fetchall()
    if len(rows) < 3:   # not enough history to be useful
        return ""

    counts: Counter = Counter()
    for row in rows:
        for w in re.findall(r"\b[A-Za-z][A-Za-z0-9'_\-]{2,}\b", row["clean_text"]):
            if w.lower() not in _STOPWORDS:
                counts[w.lower()] += 1

    # Require ≥2 appearances so random one-off words don't bias Whisper
    terms = [w for w, c in counts.most_common(max_terms * 2) if c >= 2][:max_terms]
    return " ".join(terms)


def get_usage_stats() -> dict:
    """Return per-day, per-week, and per-session usage counters."""
    with _conn() as db:
        # ── Today ─────────────────────────────────────────────
        day_row = db.execute("""
            SELECT COUNT(*) as cnt,
                   COALESCE(SUM(word_count),0)   as words,
                   COALESCE(SUM(duration_sec),0) as secs
            FROM transcriptions
            WHERE DATE(created_at) = DATE('now','localtime')
              AND status='success'
        """).fetchone()

        # ── This week (rolling 7 days) ─────────────────────────
        week_row = db.execute("""
            SELECT COUNT(*) as cnt,
                   COALESCE(SUM(word_count),0)   as words,
                   COALESCE(SUM(duration_sec),0) as secs
            FROM transcriptions
            WHERE created_at >= DATETIME('now','-7 days','localtime')
              AND status='success'
        """).fetchone()

        # ── Per-day chart (last 7 days) ────────────────────────
        daily = db.execute("""
            SELECT DATE(created_at,'localtime') as day,
                   COUNT(*) as cnt,
                   COALESCE(SUM(word_count),0) as words
            FROM transcriptions
            WHERE created_at >= DATETIME('now','-7 days','localtime')
              AND status='success'
            GROUP BY DATE(created_at,'localtime')
            ORDER BY day ASC
        """).fetchall()

        # ── Language breakdown ─────────────────────────────────
        langs = db.execute("""
            SELECT language, COUNT(*) as cnt
            FROM transcriptions WHERE status='success'
            GROUP BY language ORDER BY cnt DESC LIMIT 10
        """).fetchall()

    # Session stats are tracked in-memory since app start
    import state as _st
    session_words = getattr(_st, "_session_words", 0)
    session_count = getattr(_st, "_session_count", 0)
    session_secs  = getattr(_st, "_session_secs", 0.0)

    return {
        "today":   {"count": day_row["cnt"],  "words": day_row["words"],
                    "minutes": round(day_row["secs"] / 60, 1)},
        "week":    {"count": week_row["cnt"], "words": week_row["words"],
                    "minutes": round(week_row["secs"] / 60, 1)},
        "session": {"count": session_count,   "words": session_words,
                    "minutes": round(session_secs / 60, 1)},
        "daily_chart": [dict(r) for r in daily],
        "languages": [dict(r) for r in langs],
    }


def get_stats() -> dict:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    with _conn() as db:
        total_sess  = db.execute(
            "SELECT COUNT(*) FROM transcriptions WHERE status='success'"
        ).fetchone()[0]
        today_sess  = db.execute(
            "SELECT COUNT(*) FROM transcriptions WHERE status='success' AND created_at LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]
        total_words = db.execute(
            "SELECT COALESCE(SUM(word_count),0) FROM transcriptions WHERE status='success'"
        ).fetchone()[0]
        today_words = db.execute(
            "SELECT COALESCE(SUM(word_count),0) FROM transcriptions WHERE status='success' AND created_at LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]
        total_chars = db.execute(
            "SELECT COALESCE(SUM(char_count),0) FROM transcriptions WHERE status='success'"
        ).fetchone()[0]
        avg_dur     = db.execute(
            "SELECT COALESCE(AVG(duration_sec),0) FROM transcriptions WHERE status='success'"
        ).fetchone()[0]

        daily = db.execute("""
            SELECT DATE(created_at) AS day, COUNT(*) AS sessions, SUM(word_count) AS words
            FROM transcriptions WHERE status='success' AND created_at >= DATE('now', '-13 days')
            GROUP BY DATE(created_at) ORDER BY day ASC
        """).fetchall()

        app_dist = db.execute("""
            SELECT category, COUNT(*) AS cnt FROM transcriptions
            WHERE status='success'
            GROUP BY category ORDER BY cnt DESC LIMIT 8
        """).fetchall()

        hourly = db.execute("""
            SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hour, COUNT(*) AS cnt
            FROM transcriptions WHERE status='success' AND created_at >= DATE('now', '-7 days')
            GROUP BY hour ORDER BY hour ASC
        """).fetchall()

        recent = db.execute("""
            SELECT clean_text, target_app, word_count, created_at
            FROM transcriptions
            WHERE status='success' AND clean_text != ''
            ORDER BY created_at DESC LIMIT 5
        """).fetchall()

    return {
        "total_sessions": total_sess,
        "today_sessions": today_sess,
        "total_words":    total_words,
        "today_words":    today_words,
        "total_chars":    total_chars,
        "avg_duration":   round(avg_dur, 2),
        "daily":          [dict(r) for r in daily],
        "app_dist":       [dict(r) for r in app_dist],
        "hourly":         [dict(r) for r in hourly],
        "recent":         [dict(r) for r in recent],
    }


def get_insights() -> dict:
    """Returns data for the Insights dashboard panel."""
    from datetime import datetime, timedelta

    with _conn() as db:
        # Words per minute: sum(word_count) / sum(duration_sec/60), only success sessions
        wpm_row = db.execute("""
            SELECT COALESCE(SUM(word_count),0) as words,
                   COALESCE(SUM(duration_sec),1) as secs
            FROM transcriptions WHERE status='success' AND duration_sec > 0
        """).fetchone()
        total_wpm_words = wpm_row['words']
        total_wpm_secs  = max(wpm_row['secs'], 1)
        wpm = round(total_wpm_words / (total_wpm_secs / 60)) if total_wpm_secs > 0 else 0

        # Fixes: sessions where clean_text != raw_text (LLM changed something)
        fixes = db.execute("""
            SELECT COUNT(*) FROM transcriptions
            WHERE status='success' AND clean_text != raw_text AND clean_text != ''
        """).fetchone()[0]

        # Total words ever
        total_words = db.execute(
            "SELECT COALESCE(SUM(word_count),0) FROM transcriptions WHERE status='success'"
        ).fetchone()[0]

        # This month's words
        month_start = datetime.now().strftime("%Y-%m-01")
        month_words = db.execute(
            "SELECT COALESCE(SUM(word_count),0) FROM transcriptions "
            "WHERE created_at >= ? AND status='success'",
            (month_start,)
        ).fetchone()[0]

        # Category distribution (for usage bars)
        cat_dist = db.execute("""
            SELECT category, COUNT(*) as cnt, COALESCE(SUM(word_count),0) as words
            FROM transcriptions WHERE status='success'
            GROUP BY category ORDER BY cnt DESC
        """).fetchall()

        # Last 90 days of daily activity (for calendar)
        start_90 = (datetime.now() - timedelta(days=89)).strftime("%Y-%m-%d")
        calendar = db.execute("""
            SELECT DATE(created_at) as day,
                   COUNT(*) as sessions,
                   COALESCE(SUM(word_count),0) as words
            FROM transcriptions
            WHERE created_at >= ? AND status='success'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """, (start_90,)).fetchall()

        # Current streak
        all_days = {r['day'] for r in calendar}
        streak = 0
        check  = datetime.now().date()
        while str(check) in all_days:
            streak += 1
            check   = check - timedelta(days=1)

        # Longest streak
        sorted_days = sorted(all_days)
        max_streak  = 0
        cur_streak  = 0
        prev        = None
        for d in sorted_days:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            if prev and (dt - prev).days == 1:
                cur_streak += 1
            else:
                cur_streak = 1
            max_streak = max(max_streak, cur_streak)
            prev = dt

    return {
        "wpm":           wpm,
        "fixes":         fixes,
        "total_words":   total_words,
        "month_words":   month_words,
        "category_dist": [dict(r) for r in cat_dist],
        "calendar":      [dict(r) for r in calendar],
        "streak":        streak,
        "longest_streak": max_streak,
    }