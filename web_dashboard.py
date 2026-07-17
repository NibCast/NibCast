# ============================================================
#  NibCast — Web Dashboard (routes only)
# ============================================================
#  HTML / CSS / JS live under templates/ and static/.
#  This file is just Flask routes + the dashboard thread runner.
# ============================================================

import io
import os
import sys
import time
import threading
import zipfile
from collections import deque

from flask import (Flask, jsonify, request, Response, session, redirect,
                   render_template)

import config
import database as db
import target_manager as tm
import auth
import state
from logger import log

try:
    from audio_recorder import list_input_devices as _list_input_devices
except Exception:
    def _list_input_devices(): return []


_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(_DIR, "templates"),
    static_folder=os.path.join(_DIR, "static"),
)
app.config["JSON_SORT_KEYS"] = False
app.secret_key = auth.get_flask_secret()
# Harden the session cookie. The dashboard is localhost-only, but SameSite=Lax
# stops another website from riding an authenticated session via a cross-site
# request (CSRF / DNS-rebinding), and HttpOnly keeps page scripts from reading
# the cookie. Secure stays off: this is plain HTTP on 127.0.0.1 where TLS adds
# no benefit and would only break the cookie.
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)
DASHBOARD_PORT = 7171

# Live widget reference — set by main.py after the widget is created.
# Allows dashboard endpoints to push style/shape changes without a restart.
_widget_ref = None

def set_widget_ref(widget):
    global _widget_ref
    _widget_ref = widget


# Live wake-word control — set by main.py. Called after the config save handler
# changes any wake setting so enabling/disabling voice activation (or editing the
# wake phrase) takes effect immediately, with no app restart.
_wake_control = None

def set_wake_control(cb):
    global _wake_control
    _wake_control = cb


# ────────────────────────────────────────────────────────────
# Brute-force protection
# ────────────────────────────────────────────────────────────
_RATE_WINDOW_SEC   = 60
_RATE_MAX_ATTEMPTS = 5
_login_attempts: dict = {}     # ip -> deque of recent fail timestamps
_lock = threading.Lock()


def _is_rate_limited(ip: str) -> bool:
    """Return True if this IP has had too many failed logins recently."""
    now = time.time()
    with _lock:
        dq = _login_attempts.get(ip)
        if not dq:
            return False
        while dq and dq[0] < now - _RATE_WINDOW_SEC:
            dq.popleft()
        return len(dq) >= _RATE_MAX_ATTEMPTS


def _record_failed_login(ip: str):
    with _lock:
        dq = _login_attempts.setdefault(ip, deque())
        dq.append(time.time())


def _reset_failed(ip: str):
    with _lock:
        _login_attempts.pop(ip, None)


def _client_ip() -> str:
    # Server binds to 127.0.0.1 only — never trust X-Forwarded-For; a local
    # process can forge it to bypass per-IP rate limits.
    return request.remote_addr or "127.0.0.1"


# ────────────────────────────────────────────────────────────
# Auth middleware
# ────────────────────────────────────────────────────────────
@app.before_request
def require_login():
    public = {"/login", "/api/login", "/api/logout", "/setup", "/api/setup-pin"}
    # Allow static files through
    if request.path.startswith("/static/"):
        return
    if request.path in public:
        return
    if not auth.is_configured():
        # Hard-redirect everything to /setup so first-run can't be skipped
        if request.path.startswith("/api/"):
            return jsonify({"error": "setup-required"}), 401
        return redirect("/setup")
    if not session.get("vf_auth"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect("/login")


# ────────────────────────────────────────────────────────────
# Auth routes
# ────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not auth.is_configured():
        return redirect("/setup")

    error = ""
    ip = _client_ip()

    if request.method == "POST":
        if _is_rate_limited(ip):
            error = "TOO MANY ATTEMPTS — WAIT 60 SECONDS"
        elif auth.verify_pin(request.form.get("pin", "")):
            _reset_failed(ip)
            session["vf_auth"] = True
            return redirect("/")
        else:
            _record_failed_login(ip)
            error = "ACCESS DENIED — INVALID PIN"
    return render_template(
        "login.html", error=error,
        pattern_configured=auth.is_pattern_configured(),
        totp_configured=auth.is_totp_configured(),
    )


@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    """First-run page: forces user to create a PIN before any other route works."""
    if auth.is_configured():
        return redirect("/login")

    error = ""
    if request.method == "POST":
        pin  = request.form.get("pin", "").strip()
        pin2 = request.form.get("pin2", "").strip()
        if len(pin) < 4:
            error = "PIN MUST BE AT LEAST 4 CHARACTERS"
        elif pin != pin2:
            error = "PINS DO NOT MATCH"
        else:
            auth.setup_pin(pin)
            session["vf_auth"] = True
            log.info("🔐 Dashboard PIN configured via /setup")
            return redirect("/")
    return render_template("setup.html", error=error)


@app.route("/api/login", methods=["POST"])
def api_login():
    ip   = _client_ip()
    if _is_rate_limited(ip):
        return jsonify({"ok": False, "error": "rate-limited"}), 429
    data   = request.get_json(force=True, silent=True) or {}
    method = data.get("method", "pin")

    ok = False
    if method == "pin":
        ok = auth.verify_pin(data.get("pin", ""))
    elif method == "pattern":
        ok = auth.verify_pattern(data.get("pattern", ""))
    elif method == "totp":
        ok = auth.verify_totp(data.get("code", ""))

    if ok:
        _reset_failed(ip)
        session["vf_auth"] = True
        return jsonify({"ok": True})

    _record_failed_login(ip)
    errs = {"pin": "Invalid PIN", "pattern": "Invalid Pattern", "totp": "Invalid Code"}
    return jsonify({"ok": False, "error": errs.get(method, "Auth failed")}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/change-pin", methods=["POST"])
def api_change_pin():
    data = request.get_json(force=True, silent=True) or {}
    if auth.change_pin(data.get("old_pin", ""), data.get("new_pin", "")):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Current PIN incorrect"}), 400


@app.route("/api/setup-pin", methods=["POST"])
def api_setup_pin():
    # Block after initial setup — use /api/change-pin instead.
    if auth.is_configured():
        return jsonify({"ok": False, "error": "Already configured — use change-pin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    pin  = data.get("pin", "").strip()
    if len(pin) < 4:
        return jsonify({"ok": False, "error": "PIN must be at least 4 chars"}), 400
    auth.setup_pin(pin)
    session["vf_auth"] = True
    return jsonify({"ok": True})


@app.route("/api/setup-pattern", methods=["POST"])
def api_setup_pattern():
    data = request.get_json(force=True, silent=True) or {}
    pattern = data.get("pattern", "")
    try:
        auth.setup_pattern(pattern)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/setup-totp", methods=["POST"])
def api_setup_totp():
    try:
        result = auth.setup_totp()
        return jsonify({"ok": True, "secret": result["secret"], "uri": result["uri"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/confirm-totp", methods=["POST"])
def api_confirm_totp():
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip()
    if auth.confirm_totp(code):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Code incorrect or expired"}), 400


@app.route("/api/disable-totp", methods=["POST"])
def api_disable_totp():
    auth.disable_totp()
    return jsonify({"ok": True})


# ────────────────────────────────────────────────────────────
# First-run / onboarding health check
# Returns which keys are missing so the dashboard can show a setup banner.
# ────────────────────────────────────────────────────────────
@app.route("/api/setup-status")
def api_setup_status():
    asr_b   = getattr(config, "ASR_BACKEND", "groq")
    llm_b   = getattr(config, "LLM_BACKEND", "groq")
    missing = []
    key_map = {
        "groq":      ("GROQ_API_KEY",      "https://console.groq.com"),
        "openai":    ("OPENAI_API_KEY",     "https://platform.openai.com/api-keys"),
        "nvidia":    ("NVIDIA_API_KEY",     "https://build.nvidia.com"),
        "anthropic": ("ANTHROPIC_API_KEY",  "https://console.anthropic.com"),
        "deepgram":  ("DEEPGRAM_API_KEY",   "https://console.deepgram.com"),
        "cerebras":  ("CEREBRAS_API_KEY",   "https://cloud.cerebras.ai"),
        "gemini":    ("GEMINI_API_KEY",     "https://aistudio.google.com/apikey"),
    }
    for backend in {asr_b, llm_b}:
        entry = key_map.get(backend)
        if entry:
            key_name, signup_url = entry
            if not getattr(config, key_name, ""):
                missing.append({"key": key_name, "backend": backend, "signup": signup_url})
    return jsonify({"ok": True, "missing_keys": missing, "ready": len(missing) == 0})


# ────────────────────────────────────────────────────────────
# Data API
# ────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/insights")
def api_insights():
    return jsonify(db.get_insights())


@app.route("/api/usage-stats")
def api_usage_stats():
    return jsonify(db.get_usage_stats())


@app.route("/api/history")
def api_history():
    return jsonify(db.get_history(
        limit=int(request.args.get("limit", 500)),
        search=request.args.get("search", ""),
        category=request.args.get("category", ""),
    ))


@app.route("/api/history/<int:tid>", methods=["DELETE"])
def api_delete_one(tid):
    db.delete_transcription(tid)
    return jsonify({"ok": True})


@app.route("/api/history/clear", methods=["POST"])
def api_clear_history():
    db.clear_all_history()
    return jsonify({"ok": True})


@app.route("/api/history/export")
def api_export_csv():
    mode = request.args.get("mode", "user")
    csv_data = db.export_csv(mode=mode)
    label = "dev" if mode == "dev" else "history"
    fname = f"nibcast_{label}_{time.strftime('%Y%m%d')}.csv"
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/api/history/export-pdf")
def api_export_pdf():
    """Export history as a branded PDF.
    ?mode=dev  — developer view: all columns, raw vs clean diff, error entries.
    (default)  — user view: clean readable transcript cards only.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return jsonify({"ok": False,
                        "error": "PyMuPDF not installed. Run: pip install pymupdf"}), 500

    from collections import defaultdict

    dev_mode = request.args.get("mode", "") == "dev"
    rows = db._get_all_rows(limit=2000) if dev_mode else db.get_history(limit=2000)
    doc  = fitz.open()

    # ── Brand colours ──────────────────────────────────────────
    AMBER  = (0.91, 0.65, 0.14)   # #e8a525
    BLACK  = (0.05, 0.05, 0.05)   # near-black
    GREY   = (0.55, 0.55, 0.55)   # muted text
    WHITE  = (0.96, 0.93, 0.88)   # warm off-white text
    W, H   = 595, 842             # A4 points
    MARGIN = 52

    font_b = fitz.Font("helv")    # regular
    font_bi = fitz.Font("heit")   # oblique (italic-style)

    pages = []  # track pages for page-numbering pass

    header_label = "NIBCAST  //  DEVELOPER EXPORT" if dev_mode else "NIBCAST  //  SESSION HISTORY"

    def _new_page():
        pg = doc.new_page(width=W, height=H)
        pages.append(pg.number)  # Page objects go stale once another page is
                                 # created in this PyMuPDF version — keep the
                                 # index and re-fetch via doc[i] when needed.
        # Cards/labels are drawn assuming a dark page — paint the background
        # so WHITE/GREY text isn't rendered nearly-invisible on plain white.
        pg.draw_rect(pg.rect, color=None, fill=BLACK)
        pg.draw_rect(fitz.Rect(0, 0, W, 38), color=None, fill=AMBER)
        tw = fitz.TextWriter(pg.rect)
        tw.append((MARGIN, 25), header_label, fontsize=9, font=font_b)
        tw.write_text(pg, color=BLACK)
        return pg, 60  # (page, starting y)

    def _footer(pg, page_num, total):
        tw = fitz.TextWriter(pg.rect)
        tw.append((MARGIN, H - 24), f"Page {page_num}",
                   fontsize=8, font=font_b)
        tw.append((W - MARGIN - 60, H - 24),
                   f"Exported {time.strftime('%Y-%m-%d')}",
                   fontsize=8, font=font_b)
        tw.write_text(pg, color=GREY)
        pg.draw_line(fitz.Point(MARGIN, H - 32),
                     fitz.Point(W - MARGIN, H - 32),
                     color=AMBER, stroke_opacity=0.4, width=0.5)

    # ── Cover page ─────────────────────────────────────────────
    cover, y = _new_page()

    # Big waveform decorative bars (amber)
    BAR_HEIGHTS = [0.30, 0.55, 0.85, 1.00, 0.85, 0.55, 0.30]
    bx, bw, bmax = 310, 18, 110
    for i, ratio in enumerate(BAR_HEIGHTS):
        bh = bmax * ratio
        by = 160 + (bmax - bh) / 2
        cover.draw_rect(fitz.Rect(bx + i * (bw + 5), by,
                                   bx + i * (bw + 5) + bw, by + bh),
                        color=None, fill=AMBER,
                        fill_opacity=0.85 if i == 3 else 0.45,
                        radius=0.5)

    tw = fitz.TextWriter(cover.rect)
    tw.append((MARGIN, 120), "NibCast", fontsize=36, font=font_b)
    subtitle = "Developer Export — All Fields" if dev_mode else "Voice Dictation History"
    tw.append((MARGIN, 162), subtitle, fontsize=16, font=font_bi)
    tw.write_text(cover, color=AMBER)

    # Stats block
    by_date_all: dict = defaultdict(list)
    for r in rows:
        dk = (r.get("created_at") or "")[:10] or "Unknown"
        by_date_all[dk].append(r)

    total_words   = sum(r.get("word_count", 0) or 0 for r in rows)
    success_rows  = [r for r in rows if r.get("status") == "success"]
    error_rows    = [r for r in rows if r.get("status") not in ("success", "wake_word", "discarded", "")]
    llm_cleaned   = sum(1 for r in success_rows
                        if (r.get("raw_text") or "").strip() != (r.get("clean_text") or "").strip())
    avg_dur       = (sum(r.get("duration_sec", 0) or 0 for r in success_rows) / len(success_rows)
                     if success_rows else 0)

    # App usage ranking for dev cover
    from collections import Counter as _Counter
    app_counts = _Counter(r.get("target_app", "Unknown") for r in success_rows)

    if dev_mode:
        stats = [
            ("Total entries (all statuses)", str(len(rows))),
            ("Successful transcriptions",    str(len(success_rows))),
            ("Errors / failures",            str(len(error_rows))),
            ("LLM-cleaned entries",          f"{llm_cleaned}  ({llm_cleaned*100//max(1,len(success_rows))}%)"),
            ("Total words dictated",         f"{total_words:,}"),
            ("Avg recording duration",       f"{avg_dur:.1f}s"),
            ("Days with activity",           str(len(by_date_all))),
            ("Top app",                      app_counts.most_common(1)[0][0] if app_counts else "—"),
            ("Exported",                     time.strftime("%Y-%m-%d  %H:%M")),
        ]
    else:
        stats = [
            ("Total transcriptions", str(len(rows))),
            ("Total words dictated", f"{total_words:,}"),
            ("Days with activity",   str(len(by_date_all))),
            ("Exported",             time.strftime("%Y-%m-%d  %H:%M")),
        ]

    sy = 220
    for label, val in stats:
        tw2 = fitz.TextWriter(cover.rect)
        tw2.append((MARGIN, sy),        label, fontsize=10, font=font_bi)
        tw2.append((MARGIN + 210, sy),  val,   fontsize=10, font=font_b)
        tw2.write_text(cover, color=WHITE)
        sy += 22

    # ── Day sections ───────────────────────────────────────────
    for date_str in sorted(by_date_all.keys(), reverse=True):
        entries = by_date_all[date_str]
        page, y = _new_page()
        tw = fitz.TextWriter(page.rect)

        # Day header
        try:
            from datetime import datetime as _dt
            friendly = _dt.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d %Y")
        except Exception:
            try:
                from datetime import datetime as _dt
                friendly = _dt.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d %Y")
            except Exception:
                friendly = date_str

        tw.append((MARGIN, y), friendly, fontsize=15, font=font_b)
        y += 6
        page.draw_line(fitz.Point(MARGIN, y + 8),
                       fitz.Point(W - MARGIN, y + 8),
                       color=AMBER, width=1.0)
        y += 20

        tw.append((MARGIN, y),
                   f"{len(entries)} transcription{'s' if len(entries) != 1 else ''}  ·  "
                   f"{sum(e.get('word_count',0) or 0 for e in entries):,} words",
                   fontsize=9, font=font_bi)
        y += 22
        tw.write_text(page, color=WHITE)

        for entry in entries:
            ts         = entry.get("created_at") or ""
            time_part  = ts[11:19] if dev_mode and len(ts) >= 19 else (ts[11:16] if len(ts) >= 16 else ts)
            app        = entry.get("target_app") or "—"
            wc         = entry.get("word_count") or 0
            cc         = entry.get("char_count") or 0
            dur        = entry.get("duration_sec") or 0
            lang       = entry.get("language") or ""
            cat        = entry.get("category") or ""
            status     = entry.get("status") or ""
            err        = (entry.get("error") or "").strip()
            entry_id   = entry.get("id") or ""
            clean_text = (entry.get("clean_text") or "").strip()
            raw_text   = (entry.get("raw_text") or "").strip()
            text       = clean_text or raw_text
            llm_diff   = clean_text != raw_text and bool(clean_text) and bool(raw_text)

            CARD_PAD   = 10
            # Estimate card height including raw block in dev mode
            est_lines  = max(1, len(text) // 75 + 1)
            raw_lines  = max(1, len(raw_text) // 75 + 1) if dev_mode and llm_diff else 0
            card_h     = CARD_PAD * 2 + 14 + (est_lines + raw_lines) * 14 + (30 if dev_mode and llm_diff else 0) + 10

            if y + card_h > H - 55:
                _footer(page, len(pages), 0)
                page, y = _new_page()

            # Card background — red tint for errors in dev mode
            is_error = status not in ("success", "wake_word", "discarded", "")
            card_fill = (0.18, 0.08, 0.08) if dev_mode and is_error else (0.13, 0.13, 0.13)
            card_rect = fitz.Rect(MARGIN, y, W - MARGIN, y + card_h)
            page.draw_rect(card_rect, color=None, fill=card_fill, radius=3 / card_h)

            # Meta line
            tw3  = fitz.TextWriter(page.rect)
            if dev_mode:
                meta = (f"#{entry_id}  ·  {time_part}  ·  {app}  ·  {cat}  ·  {lang}"
                        f"  ·  {wc}w  {cc}ch  ·  {dur:.1f}s  ·  {status}")
            else:
                meta = f"{time_part}  ·  {app}  ·  {wc} words  ·  {dur:.1f}s"
            tw3.append((MARGIN + CARD_PAD, y + CARD_PAD + 2),
                        meta, fontsize=8, font=font_bi)
            tw3.write_text(page, color=GREY)

            # LLM-cleaned badge (dev mode)
            if dev_mode and llm_diff:
                badge_x = W - MARGIN - CARD_PAD - 68
                page.draw_rect(fitz.Rect(badge_x, y + CARD_PAD - 1, badge_x + 68, y + CARD_PAD + 12),
                                color=None, fill=AMBER, fill_opacity=0.25, radius=2 / 13)
                tw_b = fitz.TextWriter(page.rect)
                tw_b.append((badge_x + 4, y + CARD_PAD + 9), "✏ LLM cleaned",
                             fontsize=7, font=font_bi)
                tw_b.write_text(page, color=AMBER)

            y += CARD_PAD + 14

            def _write_wrapped(page_ref, y_ref, paragraph, fsize, color, label=None):
                """Word-wrap a paragraph, return updated y."""
                if label:
                    lbl = fitz.TextWriter(page_ref.rect)
                    lbl.append((MARGIN + CARD_PAD, y_ref), label, fontsize=8, font=font_bi)
                    lbl.write_text(page_ref, color=GREY)
                    y_ref += 12
                words_list = paragraph.split()
                line = ""
                for word in words_list:
                    candidate = (line + " " + word).strip()
                    if len(candidate) > 82:
                        if y_ref > H - 60:
                            _footer(page_ref, len(pages), 0)
                            page_ref, y_ref = _new_page()
                        tw_w = fitz.TextWriter(page_ref.rect)
                        tw_w.append((MARGIN + CARD_PAD, y_ref), line, font=font_b, fontsize=fsize)
                        tw_w.write_text(page_ref, color=color)
                        y_ref += 14
                        line = word
                    else:
                        line = candidate
                if line:
                    tw_w = fitz.TextWriter(page_ref.rect)
                    tw_w.append((MARGIN + CARD_PAD, y_ref), line, font=font_b, fontsize=fsize)
                    tw_w.write_text(page_ref, color=color)
                    y_ref += 14
                return page_ref, y_ref

            # Clean text
            if text:
                clean_label = "CLEAN:" if dev_mode and llm_diff else None
                page, y = _write_wrapped(page, y, text, 10, WHITE, label=clean_label)

            # Raw text block (dev mode only, when LLM changed it)
            if dev_mode and llm_diff and raw_text:
                y += 4
                page.draw_line(fitz.Point(MARGIN + CARD_PAD, y),
                                fitz.Point(W - MARGIN - CARD_PAD, y),
                                color=(0.25, 0.25, 0.25), width=0.5)
                y += 6
                page, y = _write_wrapped(page, y, raw_text, 9, GREY, label="RAW:")

            # Error message (dev mode)
            if dev_mode and err:
                y += 4
                tw_e = fitz.TextWriter(page.rect)
                tw_e.append((MARGIN + CARD_PAD, y), f"ERR: {err[:100]}",
                             fontsize=8, font=font_bi)
                tw_e.write_text(page, color=(0.9, 0.3, 0.3))
                y += 14

            y += CARD_PAD + 8   # bottom padding between cards

        _footer(page, len(pages), 0)

    # Back-fill page numbers now that total is known
    total_pages = len(pages)
    for i, page_idx in enumerate(pages):
        _footer(doc[page_idx], i + 1, total_pages)

    buf   = doc.tobytes()
    label = "dev_export" if dev_mode else "history"
    fname = f"nibcast_{label}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    return Response(buf, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/api/privacy", methods=["GET", "POST"])
def api_privacy():
    """Get or set privacy and data-retention settings."""
    if request.method == "GET":
        return jsonify({
            "privacy_mode":          getattr(config, "PRIVACY_MODE", False),
            "context_awareness":     getattr(config, "CONTEXT_AWARENESS", True),
            "history_auto_delete_days": getattr(config, "HISTORY_AUTO_DELETE_DAYS", 0),
        })
    data = request.get_json(force=True, silent=True) or {}
    if "privacy_mode" in data:
        config.PRIVACY_MODE = bool(data["privacy_mode"])
    if "context_awareness" in data:
        config.CONTEXT_AWARENESS = bool(data["context_awareness"])
    if "history_auto_delete_days" in data:
        try:
            config.HISTORY_AUTO_DELETE_DAYS = max(0, int(data["history_auto_delete_days"]))
        except (TypeError, ValueError):
            pass
    config.save()
    return jsonify({"ok": True})


@app.route("/api/history/purge-old", methods=["POST"])
def api_purge_old():
    """Delete transcriptions older than N days."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        days = max(1, int(data.get("days", 30)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid days value"}), 400
    deleted = db.delete_older_than(days)
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/backup-info")
def api_backup_info():
    """Return metadata about backup-able files without downloading."""
    src = config.USER_DIR
    files_info = []
    total_bytes = 0
    for root, _, files in os.walk(src):
        for f in files:
            full = os.path.join(root, f)
            try:
                size = os.path.getsize(full)
                mtime = os.path.getmtime(full)
                rel = os.path.relpath(full, start=src)
                files_info.append({
                    "name": rel,
                    "size": size,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                    "type": "config" if f.endswith(".json") else
                            "database" if f.endswith(".db") else
                            "log" if f.endswith(".log") else "other",
                })
                total_bytes += size
            except Exception:
                pass
    stats = db.get_stats()
    return jsonify({
        "files": files_info,
        "total_size": total_bytes,
        "total_size_fmt": f"{total_bytes/1024:.1f} KB" if total_bytes < 1024*1024 else f"{total_bytes/1024/1024:.2f} MB",
        "db_sessions": stats.get("total_sessions", 0),
        "dir": src,
    })


@app.route("/api/backup")
def api_backup():
    """Stream a zip. Query params: config=1, db=1, logs=1 (default all=1)."""
    include_config = request.args.get("config", "1") == "1"
    include_db     = request.args.get("db",     "1") == "1"
    include_logs   = request.args.get("logs",   "1") == "1"

    import sqlite3
    import tempfile

    buf = io.BytesIO()
    src = config.USER_DIR
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                rel  = os.path.relpath(full, start=src)
                is_cfg = f.endswith(".json")
                is_db  = f.endswith(".db")
                is_log = f.endswith(".log")
                if is_cfg and not include_config: continue
                if is_db  and not include_db:     continue
                if is_log and not include_logs:   continue
                try:
                    if is_db:
                        # Use SQLite's backup API for a clean, non-corrupt snapshot.
                        # Direct file copy of a hot SQLite database can produce a
                        # corrupted or incomplete backup.
                        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
                        os.close(tmp_fd)
                        try:
                            with sqlite3.connect(full, check_same_thread=False) as src_conn, \
                                 sqlite3.connect(tmp_path) as dst_conn:
                                src_conn.backup(dst_conn)
                            zf.write(tmp_path, arcname=rel)
                        finally:
                            try: os.unlink(tmp_path)
                            except Exception: pass
                    else:
                        zf.write(full, arcname=rel)
                except Exception as e:
                    log.warning(f"backup skip {full}: {e}")
    buf.seek(0)
    parts = []
    if include_config: parts.append("cfg")
    if include_db:     parts.append("db")
    if include_logs:   parts.append("logs")
    suffix = "_" + "+".join(parts) if parts else "_full"
    fname = f"nibcast_backup{suffix}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        buf.read(), mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


def _app_version():
    # Read the already-loaded main module's __version__ (single source of
    # truth) without re-importing it and triggering its side effects.
    for modname in ("main", "__main__"):
        v = getattr(sys.modules.get(modname), "__version__", None)
        if v:
            return v
    return "unknown"


def _on_disk_version():
    """__version__ as written in main.py on disk right now — differs from
    _app_version() when the code was updated under a still-running process."""
    import re as _re
    try:
        with open(os.path.join(_DIR, "main.py"), encoding="utf-8") as f:
            for line in f:
                m = _re.match(r'__version__\s*=\s*["\']([^"\']+)', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "unknown"


@app.route("/api/app-version")
def api_app_version():
    """Running vs on-disk app version. The dashboard uses this to warn when
    the process is older than the code on disk (an update was applied without
    restarting the app). In that state the page serves FRESH JS from disk while
    the Python routes stay OLD — new page features hit missing endpoints and
    fail in confusing ways (HTML 404s parsed as JSON, etc.). Surfacing
    \"restart NibCast\" beats every user having to rediscover that."""
    running, on_disk = _app_version(), _on_disk_version()
    return jsonify({
        "running": running,
        "on_disk": on_disk,
        "stale": "unknown" not in (running, on_disk) and running != on_disk,
    })


@app.route("/api/debug-bundle")
def api_debug_bundle():
    """A SCRUBBED diagnostics bundle for bug reports: redacted config + system
    info + the recent log. API keys are NEVER included, so it's safe to attach
    to a public GitHub issue (still skim the log for personal dictation text)."""
    import json as _json
    import platform as _plat
    import re as _re

    _SECRET_RE = _re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PIN)", _re.I)

    def _redact(obj):
        if isinstance(obj, dict):
            return {k: (f"***REDACTED (len={len(v)})***"
                       if isinstance(v, str) and v and _SECRET_RE.search(str(k))
                       else _redact(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [_redact(x) for x in obj]
        return obj

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1) Settings with every secret redacted
        try:
            with open(config.CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                cfg = _json.load(f)
            zf.writestr("config.redacted.json",
                        _json.dumps(_redact(cfg), indent=2, ensure_ascii=False))
        except FileNotFoundError:
            zf.writestr("config.redacted.json", "{}")
        except Exception as e:
            zf.writestr("config.redacted.json", f"// could not read config: {e}")

        # 2) System info — no secrets, only which keys are set
        info = [
            f"NibCast version : {_app_version()}",
            f"Generated       : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"OS              : {_plat.platform()}",
            f"Python          : {_plat.python_version()} ({_plat.architecture()[0]})",
            f"ASR backend     : {getattr(config, 'ASR_BACKEND', '?')}",
            f"LLM backend     : {getattr(config, 'LLM_BACKEND', '?')}",
            f"Brain mode      : {getattr(config, 'BRAIN_MODE', False)}",
            f"Language        : {getattr(config, 'LANGUAGE', '')!r}",
            f"Privacy mode    : {getattr(config, 'PRIVACY_MODE', False)}",
            "",
            "API keys configured (value hidden):",
        ]
        for attr in sorted(a for a in dir(config) if a.endswith("_API_KEY")):
            info.append(f"  {attr:<22}: {'set' if getattr(config, attr, '') else 'not set'}")
        zf.writestr("system_info.txt", "\n".join(info) + "\n")

        # 3) Recent log tail
        try:
            log_path = None
            for root, _, files in os.walk(config.USER_DIR):
                for fn in files:
                    if fn.endswith(".log"):
                        log_path = os.path.join(root, fn)
            if log_path:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    zf.writestr("nibcast.log", "".join(f.readlines()[-3000:]))
        except Exception as e:
            zf.writestr("nibcast.log", f"// could not read log: {e}")

        # 4) What's inside + a privacy reminder
        zf.writestr("README.txt",
            "NibCast diagnostics bundle\n"
            "==========================\n\n"
            "  system_info.txt      - versions, OS, selected backends (no secrets)\n"
            "  config.redacted.json - your settings with every API key REDACTED\n"
            "  nibcast.log          - the last ~3000 log lines\n\n"
            "API keys are never included. Before attaching this to a public issue,\n"
            "skim nibcast.log in case it contains personal dictation text (enable\n"
            "Privacy Mode to keep transcript text out of logs entirely).\n")

    buf.seek(0)
    fname = f"nibcast_debug_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        buf.read(), mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.route("/api/targets")
def api_targets():
    return jsonify({
        "targets":  tm.all_targets(),
        "override": tm.get_override(),
        "detected": tm.get_last_detected(),
    })


@app.route("/api/targets/override", methods=["POST"])
def api_set_override():
    data = request.get_json(force=True, silent=True) or {}
    cat = data.get("category", "")
    tm.set_override(cat) if cat else tm.clear_override()
    return jsonify({"ok": True, "override": tm.get_override()})


@app.route("/api/targets/rule", methods=["POST"])
def api_update_rule():
    data = request.get_json(force=True, silent=True) or {}
    tm.update_rule(data["category"], data["field"], data["value"])
    return jsonify({"ok": True})


def _mask_key(k):
    k = k or ""
    return k[:8] + "…" + k[-4:] if len(k) > 12 else ("****" if k else "")


@app.route("/api/config")
def api_get_config():
    nk = config.NVIDIA_API_KEY or ""
    ok = getattr(config, "OPENAI_API_KEY", "") or ""
    ak = getattr(config, "ANTHROPIC_API_KEY", "") or ""
    ck = getattr(config, "CUSTOM_API_KEY", "") or ""
    return jsonify({
        "NVIDIA_API_KEY_MASKED": _mask_key(nk),
        "NVIDIA_API_KEY_SET":    bool(nk),
        "HOTKEY_COMBOS":  list(config.HOTKEY_COMBOS),
        "HOTKEY_COMBO":   config.HOTKEY_COMBOS[0] if config.HOTKEY_COMBOS else "",
        "HOTKEY_CONFIGS": list(getattr(config, "HOTKEY_CONFIGS", [])),
        "HOLD_TO_TALK":   config.HOLD_TO_TALK,
        "ASR_MODEL":      config.ASR_MODEL,
        "LLM_MODEL":      config.LLM_MODEL,
        "LANGUAGE":       config.LANGUAGE,
        "WHISPER_PROMPT": getattr(config, "WHISPER_PROMPT", ""),
        "WRITING_STYLE":  getattr(config, "WRITING_STYLE", "flow"),
        "CLEAN_WITH_LLM": config.CLEAN_WITH_LLM,
        "APPEND_NEWLINE": config.APPEND_NEWLINE,
        "PRESERVE_CLIPBOARD": config.PRESERVE_CLIPBOARD,
        "EDIT_BEFORE_PASTE":  config.EDIT_BEFORE_PASTE,
        "INPUT_DEVICE":   config.INPUT_DEVICE,
        "SAMPLE_RATE":    config.SAMPLE_RATE,
        "HTTP_TIMEOUT":   config.HTTP_TIMEOUT,
        "HTTP_RETRIES":   config.HTTP_RETRIES,
        "RECORDING_MODE":        getattr(config, "RECORDING_MODE", "hold"),
        "ACTIVATION_MODE":       getattr(config, "ACTIVATION_MODE", "both"),
        "VOICE_VAD_THRESHOLD":   getattr(config, "VOICE_VAD_THRESHOLD", 0.015),
        "VOICE_VAD_SILENCE_SEC": getattr(config, "VOICE_VAD_SILENCE_SEC", 2.0),
        "auth_configured": auth.is_configured(),
        # Backends
        "ASR_BACKEND": getattr(config, "ASR_BACKEND", "groq"),
        "LLM_BACKEND": getattr(config, "LLM_BACKEND", "groq"),
        "LLM_FALLBACK_BACKEND": getattr(config, "LLM_FALLBACK_BACKEND", ""),
        "GROQ_API_KEY_MASKED": _mask_key(getattr(config, "GROQ_API_KEY", "") or ""),
        "GROQ_API_KEY_SET":    bool(getattr(config, "GROQ_API_KEY", "")),
        "GROQ_ASR_MODEL": getattr(config, "GROQ_ASR_MODEL", "whisper-large-v3-turbo"),
        "GROQ_LLM_MODEL": getattr(config, "GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
        "BRAIN_MODE":          getattr(config, "BRAIN_MODE", False),
        "ASR_BRAIN_SECONDARY": getattr(config, "ASR_BRAIN_SECONDARY", "openai"),
        "LLM_BRAIN_SECONDARY": getattr(config, "LLM_BRAIN_SECONDARY", ""),
        "OPENAI_API_KEY_MASKED": _mask_key(ok),
        "OPENAI_API_KEY_SET":    bool(ok),
        "OPENAI_ASR_MODEL": getattr(config, "OPENAI_ASR_MODEL", "whisper-1"),
        "OPENAI_LLM_MODEL": getattr(config, "OPENAI_LLM_MODEL", "gpt-4o-mini"),
        "ANTHROPIC_API_KEY_MASKED": _mask_key(ak),
        "ANTHROPIC_API_KEY_SET":    bool(ak),
        "ANTHROPIC_LLM_MODEL": getattr(config, "ANTHROPIC_LLM_MODEL", "claude-3-5-haiku-20241022"),
        "CEREBRAS_API_KEY_MASKED": _mask_key(getattr(config, "CEREBRAS_API_KEY", "") or ""),
        "CEREBRAS_API_KEY_SET":    bool(getattr(config, "CEREBRAS_API_KEY", "")),
        "CEREBRAS_LLM_MODEL": getattr(config, "CEREBRAS_LLM_MODEL", "llama-3.3-70b"),
        "GEMINI_API_KEY_MASKED": _mask_key(getattr(config, "GEMINI_API_KEY", "") or ""),
        "GEMINI_API_KEY_SET":    bool(getattr(config, "GEMINI_API_KEY", "")),
        "GEMINI_LLM_MODEL": getattr(config, "GEMINI_LLM_MODEL", "gemini-2.5-flash"),
        "OLLAMA_BASE_URL":  getattr(config, "OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "OLLAMA_LLM_MODEL": getattr(config, "OLLAMA_LLM_MODEL", "llama3.2"),
        "LOCAL_ASR_URL":   getattr(config, "LOCAL_ASR_URL", "http://localhost:8000/v1/audio/transcriptions"),
        "LOCAL_ASR_MODEL": getattr(config, "LOCAL_ASR_MODEL", "whisper-1"),
        "CUSTOM_API_KEY_MASKED": _mask_key(ck),
        "CUSTOM_API_KEY_SET":    bool(ck),
        "CUSTOM_ASR_URL":   getattr(config, "CUSTOM_ASR_URL", ""),
        "CUSTOM_ASR_MODEL": getattr(config, "CUSTOM_ASR_MODEL", ""),
        "CUSTOM_LLM_URL":   getattr(config, "CUSTOM_LLM_URL", ""),
        "CUSTOM_LLM_MODEL": getattr(config, "CUSTOM_LLM_MODEL", ""),
        # Deepgram
        "DEEPGRAM_API_KEY_MASKED": _mask_key(getattr(config, "DEEPGRAM_API_KEY", "") or ""),
        "DEEPGRAM_API_KEY_SET":    bool(getattr(config, "DEEPGRAM_API_KEY", "")),
        "DEEPGRAM_ASR_MODEL":  getattr(config, "DEEPGRAM_ASR_MODEL", "nova-3"),
        "DEEPGRAM_DIARIZE":    getattr(config, "DEEPGRAM_DIARIZE", False),
        # Wake word
        "WAKE_WORD":              getattr(config, "WAKE_WORD", ""),
        "WAKE_WORD_ENABLED":      getattr(config, "WAKE_WORD_ENABLED", False),
        "WAKE_WORD_VAD_THRESHOLD": getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.05),
        "WAKE_AUTO_RAISE_ENABLED": getattr(config, "WAKE_AUTO_RAISE_ENABLED", True),
        "WAKE_WORD_SILENCE_SEC":    getattr(config, "WAKE_WORD_SILENCE_SEC", 0.55),
        "WAKE_WORD_TRIGGER_SEC":    getattr(config, "WAKE_WORD_TRIGGER_SEC", 0.15),
        "WAKE_WORD_MAX_RECORD_SEC": getattr(config, "WAKE_WORD_MAX_RECORD_SEC", 2.5),
        "WAKE_WORD_LISTEN_SEC":     getattr(config, "WAKE_WORD_LISTEN_SEC", 12.0),
        "VOICE_SIMILARITY_THRESHOLD": getattr(config, "VOICE_SIMILARITY_THRESHOLD", 0.62),
        "VOICE_ENROLLMENT_ENABLED":   getattr(config, "VOICE_ENROLLMENT_ENABLED", True),
        # UI
        "WIDGET_STYLE": getattr(config, "WIDGET_STYLE", "wave"),
        "WIDGET_THEME": getattr(config, "WIDGET_THEME", "amber"),
        # Startup
        "START_MINIMIZED":      getattr(config, "START_MINIMIZED", False),
        "SHOW_WIDGET_ON_START": getattr(config, "SHOW_WIDGET_ON_START", True),
        # Audio cues
        "AUDIO_CUES":      getattr(config, "AUDIO_CUES", True),
        "AUDIO_CUE_START": getattr(config, "AUDIO_CUE_START", True),
        "AUDIO_CUE_STOP":  getattr(config, "AUDIO_CUE_STOP", True),
        "AUDIO_CUE_ERROR": getattr(config, "AUDIO_CUE_ERROR", True),
        # Privacy
        "PRIVACY_MODE":             getattr(config, "PRIVACY_MODE", False),
        "CONTEXT_AWARENESS":        getattr(config, "CONTEXT_AWARENESS", True),
        "HISTORY_AUTO_DELETE_DAYS": getattr(config, "HISTORY_AUTO_DELETE_DAYS", 0),
    })


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json(force=True, silent=True) or {}
    if "NVIDIA_API_KEY" in data and (data["NVIDIA_API_KEY"] or "").strip():
        config.NVIDIA_API_KEY = data["NVIDIA_API_KEY"].strip()
    if "HOTKEY_CONFIGS" in data and isinstance(data["HOTKEY_CONFIGS"], list):
        configs = [
            {"combo": item["combo"].strip(), "mode": item.get("mode", "hold")}
            for item in data["HOTKEY_CONFIGS"]
            if isinstance(item, dict)
            and (item.get("combo") or "").strip()
            and item.get("mode", "hold") in ("hold", "toggle")
        ]
        if configs:
            config.HOTKEY_CONFIGS = configs
            config.HOTKEY_COMBOS  = [c["combo"] for c in configs]
            config.HOTKEY_COMBO   = config.HOTKEY_COMBOS[0] if config.HOTKEY_COMBOS else ""
    elif "HOTKEY_COMBOS" in data and isinstance(data["HOTKEY_COMBOS"], list):
        cleaned = [c.strip() for c in data["HOTKEY_COMBOS"] if c and c.strip()]
        if cleaned:
            config.HOTKEY_COMBOS = cleaned
            config.HOTKEY_COMBO  = cleaned[0]
    elif "HOTKEY_COMBO" in data and (data["HOTKEY_COMBO"] or "").strip():
        combo = data["HOTKEY_COMBO"].strip()
        config.HOTKEY_COMBOS = [combo]
        config.HOTKEY_COMBO  = combo
    if "HOLD_TO_TALK"       in data: config.HOLD_TO_TALK       = bool(data["HOLD_TO_TALK"])
    if "ASR_MODEL"          in data: config.ASR_MODEL          = data["ASR_MODEL"]
    if "LLM_MODEL"          in data: config.LLM_MODEL          = data["LLM_MODEL"]
    if "LANGUAGE"           in data: config.LANGUAGE           = data["LANGUAGE"]
    if "WHISPER_PROMPT"     in data: config.WHISPER_PROMPT     = str(data["WHISPER_PROMPT"])
    if "WRITING_STYLE" in data and data["WRITING_STYLE"] in ("flow", "verbatim", "professional", "concise"):
        config.WRITING_STYLE = data["WRITING_STYLE"]
    if "CLEAN_WITH_LLM"     in data: config.CLEAN_WITH_LLM     = bool(data["CLEAN_WITH_LLM"])
    if "APPEND_NEWLINE"     in data: config.APPEND_NEWLINE     = bool(data["APPEND_NEWLINE"])
    if "PRESERVE_CLIPBOARD" in data: config.PRESERVE_CLIPBOARD = bool(data["PRESERVE_CLIPBOARD"])
    if "EDIT_BEFORE_PASTE"  in data: config.EDIT_BEFORE_PASTE  = bool(data["EDIT_BEFORE_PASTE"])
    if "AUDIO_CUES"         in data: config.AUDIO_CUES         = bool(data["AUDIO_CUES"])
    if "INPUT_DEVICE" in data:
        v = data["INPUT_DEVICE"]
        config.INPUT_DEVICE = None if v in ("", None, "null") else int(v)
    if "HTTP_TIMEOUT" in data:
        try: config.HTTP_TIMEOUT = max(5, int(data["HTTP_TIMEOUT"]))
        except (TypeError, ValueError): pass
    if "HTTP_RETRIES" in data:
        try: config.HTTP_RETRIES = max(1, min(10, int(data["HTTP_RETRIES"])))
        except (TypeError, ValueError): pass
    if "RECORDING_MODE" in data and data["RECORDING_MODE"] in ("hold", "toggle", "voice"):
        config.RECORDING_MODE = data["RECORDING_MODE"]
        # Keep legacy fields in sync for backward compat
        if data["RECORDING_MODE"] == "voice":
            config.ACTIVATION_MODE = "voice"
            config.HOLD_TO_TALK    = True
        elif data["RECORDING_MODE"] == "hold":
            config.ACTIVATION_MODE = "hotkey"
            config.HOLD_TO_TALK    = True
        elif data["RECORDING_MODE"] == "toggle":
            config.ACTIVATION_MODE = "hotkey"
            config.HOLD_TO_TALK    = False
    if "ACTIVATION_MODE" in data and data["ACTIVATION_MODE"] in ("hotkey", "click", "voice", "both"):
        config.ACTIVATION_MODE = data["ACTIVATION_MODE"]
    if "VOICE_VAD_THRESHOLD" in data:
        # Same ceiling as the wake threshold — the dashboard drives both keys
        # from one slider, so keep their accepted ranges identical.
        try: config.VOICE_VAD_THRESHOLD = max(0.001, min(getattr(config, "WAKE_WORD_VAD_THRESHOLD_MAX", 0.30), float(data["VOICE_VAD_THRESHOLD"])))
        except (TypeError, ValueError): pass
    if "VOICE_VAD_SILENCE_SEC" in data:
        try: config.VOICE_VAD_SILENCE_SEC = max(0.5, float(data["VOICE_VAD_SILENCE_SEC"]))
        except (TypeError, ValueError): pass
    if "VOICE_SIMILARITY_THRESHOLD" in data:
        # How closely a command must match the enrolled voice. Lower = more
        # forgiving (accepts the real user even on an off day); higher = stricter.
        try: config.VOICE_SIMILARITY_THRESHOLD = max(0.30, min(0.95, float(data["VOICE_SIMILARITY_THRESHOLD"])))
        except (TypeError, ValueError): pass
    if "VOICE_ENROLLMENT_ENABLED" in data:
        config.VOICE_ENROLLMENT_ENABLED = bool(data["VOICE_ENROLLMENT_ENABLED"])

    # Backends
    # NVIDIA is intentionally NOT a valid ASR backend — integrate.api.nvidia.com
    # has no audio/transcription endpoint (every request 404s). It stays valid
    # for LLM only. Reject the selection so users can't pick a backend that
    # cannot work.
    if "ASR_BACKEND" in data and data["ASR_BACKEND"] in ("groq", "openai", "local", "custom", "deepgram"):
        config.ASR_BACKEND = data["ASR_BACKEND"]
    if "LLM_BACKEND" in data and data["LLM_BACKEND"] in ("groq", "cerebras", "gemini", "nvidia", "openai", "ollama", "anthropic", "custom"):
        config.LLM_BACKEND = data["LLM_BACKEND"]
    if "LLM_FALLBACK_BACKEND" in data and data["LLM_FALLBACK_BACKEND"] in ("", "groq", "cerebras", "gemini", "nvidia", "openai", "ollama", "anthropic", "custom"):
        config.LLM_FALLBACK_BACKEND = data["LLM_FALLBACK_BACKEND"]
    if "DEEPGRAM_DIARIZE" in data:
        config.DEEPGRAM_DIARIZE = bool(data["DEEPGRAM_DIARIZE"])
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CUSTOM_API_KEY",
              "DEEPGRAM_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY"):
        if k in data and (data[k] or "").strip():
            setattr(config, k, data[k].strip())
    for k in ("GROQ_ASR_MODEL", "GROQ_LLM_MODEL",
              "OPENAI_ASR_MODEL", "OPENAI_LLM_MODEL",
              "ANTHROPIC_LLM_MODEL", "OLLAMA_BASE_URL", "OLLAMA_LLM_MODEL",
              "CEREBRAS_LLM_MODEL", "GEMINI_LLM_MODEL",
              "LOCAL_ASR_URL", "LOCAL_ASR_MODEL",
              "CUSTOM_ASR_URL", "CUSTOM_ASR_MODEL", "CUSTOM_LLM_URL", "CUSTOM_LLM_MODEL",
              "DEEPGRAM_ASR_MODEL",
              "ASR_BRAIN_SECONDARY", "LLM_BRAIN_SECONDARY"):
        if k in data and data[k] is not None:
            setattr(config, k, data[k])
    if "BRAIN_MODE" in data:
        config.BRAIN_MODE = bool(data["BRAIN_MODE"])

    # Wake word
    _wake_changed = False
    if "WAKE_WORD" in data:
        config.WAKE_WORD = str(data["WAKE_WORD"]).strip()
        _wake_changed = True
    if "WAKE_WORD_ENABLED" in data:
        config.WAKE_WORD_ENABLED = bool(data["WAKE_WORD_ENABLED"])
        _wake_changed = True
    if "WAKE_WORD_VAD_THRESHOLD" in data:
        # Clamp to the same ceiling the VAD engine enforces, so the saved value
        # is always the effective value — never persist a threshold the engine
        # would silently clamp away.
        try:
            _thr_max = getattr(config, "WAKE_WORD_VAD_THRESHOLD_MAX", 0.30)
            _old_thr = getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.03)
            config.WAKE_WORD_VAD_THRESHOLD = max(0.01, min(_thr_max, float(data["WAKE_WORD_VAD_THRESHOLD"])))
            # A hand-moved slider is an explicit user choice — stop the ambient
            # auto-raise from overwriting it. (An explicit WAKE_AUTO_RAISE_ENABLED
            # in the same payload wins below, so re-enabling still works.)
            if abs(config.WAKE_WORD_VAD_THRESHOLD - _old_thr) > 1e-6:
                config.WAKE_AUTO_RAISE_ENABLED = False
        except (TypeError, ValueError):
            pass
    if "WAKE_AUTO_RAISE_ENABLED" in data:
        config.WAKE_AUTO_RAISE_ENABLED = bool(data["WAKE_AUTO_RAISE_ENABLED"])
    if "WAKE_WORD_SILENCE_SEC" in data:
        # Floor matches the engine clamp (voice_activator.py sleep branch uses
        # max(0.3, …)) so the saved value is always the effective value.
        try: config.WAKE_WORD_SILENCE_SEC = max(0.3, float(data["WAKE_WORD_SILENCE_SEC"]))
        except (TypeError, ValueError): pass
    if "WAKE_WORD_TRIGGER_SEC" in data:
        try: config.WAKE_WORD_TRIGGER_SEC = max(0.05, float(data["WAKE_WORD_TRIGGER_SEC"]))
        except (TypeError, ValueError): pass
    if "WAKE_WORD_MAX_RECORD_SEC" in data:
        try: config.WAKE_WORD_MAX_RECORD_SEC = max(1.0, float(data["WAKE_WORD_MAX_RECORD_SEC"]))
        except (TypeError, ValueError): pass
    if "WAKE_WORD_LISTEN_SEC" in data:
        try: config.WAKE_WORD_LISTEN_SEC = max(3.0, float(data["WAKE_WORD_LISTEN_SEC"]))
        except (TypeError, ValueError): pass

    # UI
    if "WIDGET_STYLE" in data and data["WIDGET_STYLE"] in ("wave", "orbit", "pulse"):
        config.WIDGET_STYLE = data["WIDGET_STYLE"]
    if "WIDGET_THEME" in data and data["WIDGET_THEME"] in ("amber", "violet", "cyan"):
        config.WIDGET_THEME = data["WIDGET_THEME"]

    # Startup
    if "START_MINIMIZED" in data:
        config.START_MINIMIZED = bool(data["START_MINIMIZED"])
    if "SHOW_WIDGET_ON_START" in data:
        config.SHOW_WIDGET_ON_START = bool(data["SHOW_WIDGET_ON_START"])

    # Audio cues
    if "AUDIO_CUE_START" in data:
        config.AUDIO_CUE_START = bool(data["AUDIO_CUE_START"])
    if "AUDIO_CUE_STOP" in data:
        config.AUDIO_CUE_STOP = bool(data["AUDIO_CUE_STOP"])
    if "AUDIO_CUE_ERROR" in data:
        config.AUDIO_CUE_ERROR = bool(data["AUDIO_CUE_ERROR"])

    # Privacy
    if "PRIVACY_MODE" in data:
        config.PRIVACY_MODE = bool(data["PRIVACY_MODE"])
    if "CONTEXT_AWARENESS" in data:
        config.CONTEXT_AWARENESS = bool(data["CONTEXT_AWARENESS"])
    if "HISTORY_AUTO_DELETE_DAYS" in data:
        try:
            config.HISTORY_AUTO_DELETE_DAYS = max(0, int(data["HISTORY_AUTO_DELETE_DAYS"]))
        except (TypeError, ValueError):
            pass

    config.save()

    # Apply wake-word changes live so the user doesn't have to restart the app
    # for "voice activation" to start (or stop) listening.
    wake_active = None
    if _wake_changed and _wake_control is not None:
        try:
            wake_active = bool(_wake_control())
        except Exception as e:
            log.warning(f"Live wake-word apply failed: {e}")

    resp = {"ok": True}
    if wake_active is not None:
        resp["wake_active"] = wake_active
    return jsonify(resp)


@app.route("/api/devices")
def api_devices():
    return jsonify({"devices": _list_input_devices()})


@app.route("/api/test-wake-phrase")
def api_test_wake_phrase():
    """Test whether a given phrase would trigger the configured wake word.
    Used by the dashboard 'Test' button so users can verify recognition without
    restarting the app.  Pass ?text=hey+flow or POST JSON {text: "hey flow"}.

    NOTE: do NOT `from main import _match_wake_word`. main.py is the entry
    point (loaded as __main__, not as a module named "main"), so that import
    would re-execute the entire file as a second module — re-running
    db.init_db(), re-creating AudioRecorder/Transcriber/FloatingWidget/etc.,
    and (with the single-instance guard) popping a blocking "already running"
    dialog on every click. Pull the function off the live __main__ module
    instead — it's the same process, already fully initialized.
    """
    _main = sys.modules.get("__main__")
    _match_wake_word = getattr(_main, "_match_wake_word", None)
    if _match_wake_word is None:
        return jsonify({"ok": False, "error": "Wake word tester unavailable in this mode"})

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
    else:
        text = request.args.get("text", "").strip()

    if not text:
        return jsonify({"ok": False, "error": "No text provided"})

    wake_word = (getattr(config, "WAKE_WORD", "") or "").strip()
    if not wake_word:
        return jsonify({"ok": False, "error": "No wake word configured"})

    matched, remaining = _match_wake_word(text, wake_word)
    alts_matched = []
    if not matched:
        for alt in getattr(config, "WAKE_WORD_ALTERNATIVES", []):
            am, _ = _match_wake_word(text, alt)
            if am:
                alts_matched.append(alt)
                matched = True
                break

    return jsonify({
        "ok":         matched,
        "text":       text,
        "wake_word":  wake_word,
        "matched":    matched,
        "remaining":  remaining,
        "via_alt":    alts_matched[0] if alts_matched else None,
        "message":    "✅ Would trigger wake word" if matched else "❌ Would NOT trigger — try saying it differently",
    })


@app.route("/api/test-wake-phrase", methods=["POST"])
def api_test_wake_phrase_post():
    return api_test_wake_phrase()


@app.route("/api/test-api-key", methods=["GET", "POST"])
def api_test_key():
    """Connectivity + auth check. Tests the active ASR backend.

    Accepts an optional JSON body {"key": "..."} override so the dashboard
    can test a key the user just typed but hasn't saved yet — otherwise this
    would always test the last-saved value and report "not set" for a
    freshly-typed key. Sent via POST body (not a query string) so the key
    never ends up in access logs or browser history.
    """
    import requests as req
    backend = getattr(config, "ASR_BACKEND", "groq")
    typed_key = ""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        typed_key = (body.get("key", "") or "").strip()
        # Test the backend the user is actually entering a key for — NOT the
        # saved active backend. Without this, adding (say) a Deepgram key while
        # Groq is still the saved primary tests Groq with the Deepgram key and
        # reports a misleading "Invalid Groq key" / wrong-provider error.
        req_backend = (body.get("backend", "") or "").strip().lower()
        if req_backend in ("groq", "openai", "deepgram", "local", "custom", "ollama", "nvidia"):
            backend = req_backend

    if backend == "groq":
        key = typed_key or (getattr(config, "GROQ_API_KEY", "") or "").strip()
        if not key:
            return jsonify({"ok": False, "error": "GROQ_API_KEY not set — get a free key at console.groq.com"})
        test_url = "https://api.groq.com/openai/v1/models"
        backend_label = "Groq"
    elif backend == "openai":
        key = typed_key or (getattr(config, "OPENAI_API_KEY", "") or "").strip()
        if not key:
            return jsonify({"ok": False, "error": "OPENAI_API_KEY not set"})
        test_url = "https://api.openai.com/v1/models"
        backend_label = "OpenAI"
    elif backend == "deepgram":
        key = typed_key or (getattr(config, "DEEPGRAM_API_KEY", "") or "").strip()
        if not key:
            return jsonify({"ok": False, "error": "DEEPGRAM_API_KEY not set — get a free key at console.deepgram.com"})
        test_url = "https://api.deepgram.com/v1/projects"
        try:
            r = req.get(test_url, headers={"Authorization": f"Token {key}"}, timeout=8)
            if r.status_code == 200:
                return jsonify({"ok": True, "message": "Deepgram API key valid"})
            elif r.status_code == 401:
                return jsonify({"ok": False, "error": "Invalid Deepgram API key (401)"})
            else:
                return jsonify({"ok": False, "error": f"Deepgram returned HTTP {r.status_code}"})
        except Exception as e:
            return jsonify({"ok": False, "error": f"Deepgram unreachable: {e}"})
    elif backend in ("local", "custom", "ollama"):
        return jsonify({"ok": True, "message": f"{backend.upper()} backend — no remote key needed"})
    elif backend == "nvidia":
        # NVIDIA has no audio/transcription endpoint — never claim it works for ASR.
        return jsonify({"ok": False, "error": "NVIDIA does not support transcription (ASR). "
                                              "Switch ASR to Groq (free) or OpenAI in Config."})
    else:
        return jsonify({"ok": False, "error": f"Unknown ASR backend '{backend}'"})

    try:
        r = req.get(test_url, headers={"Authorization": f"Bearer {key}"}, timeout=8)
        if r.status_code == 200:
            return jsonify({"ok": True, "message": f"API key valid — {backend_label} reachable"})
        elif r.status_code == 401:
            return jsonify({"ok": False, "error": f"Invalid API key (401) for {backend_label}"})
        else:
            return jsonify({"ok": False, "error": f"{backend_label} returned HTTP {r.status_code}"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Connection failed: {str(e)[:120]}"})


@app.route("/api/test-llm", methods=["POST"])
def api_test_llm():
    """Test the active LLM backend end-to-end with a real cleanup request.
    Returns the cleaned output so the user can verify quality, not just connectivity."""
    from text_processor import TextProcessor

    # Test the backend the user is entering a key for — not just the saved
    # active LLM backend. Override config.LLM_BACKEND for the duration of this
    # request so TextProcessor.clean() targets the right provider, then restore.
    body = request.get_json(silent=True) or {}
    req_backend = (body.get("backend", "") or "").strip().lower()
    _valid_llm = ("groq", "cerebras", "gemini", "nvidia", "openai", "ollama", "anthropic", "custom")
    _saved_backend = getattr(config, "LLM_BACKEND", "groq")
    _override = req_backend in _valid_llm
    if _override:
        config.LLM_BACKEND = req_backend
    try:
        return _run_test_llm()
    finally:
        if _override:
            config.LLM_BACKEND = _saved_backend


def _run_test_llm():
    from text_processor import TextProcessor

    backend = getattr(config, "LLM_BACKEND", "groq")
    test_input = (
        "uh so basically i was trying to check whether the uh groq api is working properly "
        "and also i wanted to make sure that the nibcast application is uh functioning correctly "
        "you know like the llm cleanup and stuff"
    )

    n = backend.strip().lower()

    # Quick connectivity check first
    if n == "anthropic":
        key = getattr(config, "ANTHROPIC_API_KEY", "").strip()
        if not key:
            return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY not set"})
    elif n in ("local", "ollama"):
        pass  # no key needed
    elif n == "groq":
        if not getattr(config, "GROQ_API_KEY", "").strip():
            return jsonify({"ok": False, "error": "GROQ_API_KEY not set — get a free key at console.groq.com"})
    elif n == "openai":
        if not getattr(config, "OPENAI_API_KEY", "").strip():
            return jsonify({"ok": False, "error": "OPENAI_API_KEY not set"})
    elif n == "nvidia":
        if not (config.NVIDIA_API_KEY or "").strip():
            return jsonify({"ok": False, "error": "NVIDIA_API_KEY not set"})
    elif n == "cerebras":
        if not getattr(config, "CEREBRAS_API_KEY", "").strip():
            return jsonify({"ok": False, "error": "CEREBRAS_API_KEY not set — get a free key at cloud.cerebras.ai"})
    elif n == "gemini":
        if not getattr(config, "GEMINI_API_KEY", "").strip():
            return jsonify({"ok": False, "error": "GEMINI_API_KEY not set — get a free key at aistudio.google.com"})
    elif n == "custom":
        if not getattr(config, "CUSTOM_LLM_URL", "").strip():
            return jsonify({"ok": False, "error": "CUSTOM_LLM_URL not set"})

    try:
        tp = TextProcessor()
        result = tp.clean(test_input)
        if not result:
            return jsonify({"ok": False, "error": f"LLM backend '{backend}' returned empty — check key and model"})
        return jsonify({
            "ok":      True,
            "backend": backend,
            "input":   test_input,
            "output":  result,
            "message": f"{backend.upper()} LLM working correctly",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/transcribe-test", methods=["POST"])
def api_transcribe_test():
    """Test the active ASR pipeline with a tiny silent WAV file."""
    import io, wave, struct, requests as req
    backend = getattr(config, "ASR_BACKEND", "groq")

    if backend == "groq":
        key = (getattr(config, "GROQ_API_KEY", "") or "").strip()
        url = getattr(config, "GROQ_ASR_URL", "https://api.groq.com/openai/v1/audio/transcriptions")
        model = getattr(config, "GROQ_ASR_MODEL", "whisper-large-v3-turbo")
        if not key:
            return jsonify({"ok": False, "error": "GROQ_API_KEY not set"})
    elif backend == "openai":
        key = (getattr(config, "OPENAI_API_KEY", "") or "").strip()
        url = getattr(config, "OPENAI_ASR_URL", "https://api.openai.com/v1/audio/transcriptions")
        model = getattr(config, "OPENAI_ASR_MODEL", "whisper-1")
    elif backend == "local":
        key = ""
        url = getattr(config, "LOCAL_ASR_URL", "http://localhost:8000/v1/audio/transcriptions")
        model = getattr(config, "LOCAL_ASR_MODEL", "whisper-1")
    elif backend == "custom":
        key = getattr(config, "CUSTOM_API_KEY", "") or ""
        url = getattr(config, "CUSTOM_ASR_URL", "")
        model = getattr(config, "CUSTOM_ASR_MODEL", "")
        if not url:
            return jsonify({"ok": False, "error": "CUSTOM_ASR_URL not set"})
    elif backend == "nvidia":
        # NVIDIA has no audio/transcription endpoint — every request 404s.
        return jsonify({"ok": False, "error": "NVIDIA does not support transcription (ASR). "
                                              "Switch ASR to Groq (free) or OpenAI in Config."})
    else:
        return jsonify({"ok": False, "error": f"Unknown ASR backend '{backend}'"})

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))
    buf.seek(0)

    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    form_data = {"response_format": "json"}
    if model:
        form_data["model"] = model

    try:
        r = req.post(url, headers=headers,
                     files={"file": ("test.wav", buf.read(), "audio/wav")},
                     data=form_data, timeout=15)
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type","").startswith("application/json") else {"text": r.text[:100]}
            return jsonify({"ok": True, "message": f"ASR endpoint reachable (backend={backend})", "raw": data})
        return jsonify({"ok": False, "error": f"ASR HTTP {r.status_code}: {r.text[:300]}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]})


@app.route("/api/state")
def api_state():
    data = state.snapshot()
    upd  = state.get_update_available()
    if upd:
        data["update_available"] = upd
    return jsonify(data)


@app.route("/api/llm-stream")
def api_llm_stream():
    """Server-Sent Events: streams LLM tokens as they're generated.
    Dashboard subscribes to show live text while the LLM is processing."""
    import text_processor as tp

    q = tp.subscribe_stream()

    def generate():
        try:
            while True:
                token = q.get(timeout=35)
                if token is None:
                    yield "data: [DONE]\n\n"
                    break
                import json as _json
                yield f"data: {_json.dumps({'t': token})}\n\n"
        except Exception:
            yield "data: [DONE]\n\n"
        finally:
            tp.unsubscribe_stream(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":      "keep-alive",
        },
    )


@app.route("/api/backend-status")
def api_backend_status():
    """Quick connectivity test for all configured backends (5s timeout each)."""
    import requests as req
    results = {}

    def _test(name, url, key=None, scheme="Bearer"):
        try:
            hdrs = {"Authorization": f"{scheme} {key}"} if key else {}
            r = req.get(url, headers=hdrs, timeout=5)
            results[name] = {"ok": r.status_code == 200, "status": r.status_code}
        except Exception as e:
            results[name] = {"ok": False, "status": str(e)[:60]}

    import threading
    threads = []

    groq_key = getattr(config, "GROQ_API_KEY", "")
    if groq_key:
        threads.append(threading.Thread(target=_test, args=("groq", "https://api.groq.com/openai/v1/models", groq_key), daemon=True))
    else:
        results["groq"] = {"ok": False, "status": "no_key"}

    nvidia_key = config.NVIDIA_API_KEY or ""
    if nvidia_key:
        threads.append(threading.Thread(target=_test, args=("nvidia", "https://integrate.api.nvidia.com/v1/models", nvidia_key), daemon=True))
    else:
        results["nvidia"] = {"ok": False, "status": "no_key"}

    openai_key = getattr(config, "OPENAI_API_KEY", "")
    if openai_key:
        threads.append(threading.Thread(target=_test, args=("openai", "https://api.openai.com/v1/models", openai_key), daemon=True))
    else:
        results["openai"] = {"ok": False, "status": "no_key"}

    anthropic_key = getattr(config, "ANTHROPIC_API_KEY", "")
    if anthropic_key:
        threads.append(threading.Thread(target=_test, args=("anthropic", "https://api.anthropic.com/v1/models", anthropic_key), daemon=True))
    else:
        results["anthropic"] = {"ok": False, "status": "no_key"}

    # Deepgram uses "Token <key>" auth (not Bearer) and a /v1/projects endpoint.
    deepgram_key = getattr(config, "DEEPGRAM_API_KEY", "")
    if deepgram_key:
        threads.append(threading.Thread(target=_test, args=("deepgram", "https://api.deepgram.com/v1/projects", deepgram_key, "Token"), daemon=True))
    else:
        results["deepgram"] = {"ok": False, "status": "no_key"}

    cerebras_key = getattr(config, "CEREBRAS_API_KEY", "")
    if cerebras_key:
        threads.append(threading.Thread(target=_test, args=("cerebras", "https://api.cerebras.ai/v1/models", cerebras_key), daemon=True))
    else:
        results["cerebras"] = {"ok": False, "status": "no_key"}

    gemini_key = getattr(config, "GEMINI_API_KEY", "")
    if gemini_key:
        threads.append(threading.Thread(target=_test, args=("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/models", gemini_key), daemon=True))
    else:
        results["gemini"] = {"ok": False, "status": "no_key"}

    # Local / Ollama check
    threads.append(threading.Thread(target=_test, args=("ollama", "http://localhost:11434/api/tags"), daemon=True))
    local_url = getattr(config, "LOCAL_ASR_URL", "").replace("/v1/audio/transcriptions", "")
    if local_url:
        threads.append(threading.Thread(target=_test, args=("local_asr", local_url), daemon=True))

    for t in threads: t.start()
    for t in threads: t.join(timeout=6)

    return jsonify(results)


@app.route("/api/autostart", methods=["GET", "POST"])
def api_autostart():
    """Windows registry auto-start toggle. Supports minimized (background) mode."""
    import sys, platform
    if platform.system() != "Windows":
        return jsonify({"ok": False, "error": "Windows only"})

    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
        app_name = "NibCast"

        if request.method == "GET":
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
                val, _ = winreg.QueryValueEx(key, app_name)
                winreg.CloseKey(key)
                minimized = "--minimized" in (val or "")
                return jsonify({"ok": True, "enabled": True, "minimized": minimized})
            except FileNotFoundError:
                return jsonify({"ok": True, "enabled": False, "minimized": False})

        data      = request.get_json(force=True, silent=True) or {}
        enable    = bool(data.get("enable", False))
        minimized = bool(data.get("minimized", False))
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            flags    = " --minimized" if minimized else ""
            app_path = f'"{sys.executable}" "{os.path.abspath("main.py")}"{flags}'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
        else:
            try: winreg.DeleteValue(key, app_name)
            except FileNotFoundError: pass
        winreg.CloseKey(key)
        # Persist the minimized preference to config
        config.START_MINIMIZED = minimized
        config.save()
        return jsonify({"ok": True, "enabled": enable, "minimized": minimized})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Real-time microphone level ────────────────────────────────
_mic_level: float = 0.0


def update_mic_level(rms: float):
    global _mic_level
    _mic_level = rms


@app.route("/api/mic-level")
def api_mic_level():
    """Return current microphone RMS energy and the configured VAD threshold.
    Use this to calibrate WAKE_WORD_VAD_THRESHOLD — speak and watch the level."""
    return jsonify({
        "rms":       round(_mic_level, 4),
        "threshold": getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.05),
        "above":     _mic_level > getattr(config, "WAKE_WORD_VAD_THRESHOLD", 0.05),
    })


# ── Voice enrollment ──────────────────────────────────────────
_enrollment_session = None
_enrollment_lock    = threading.Lock()


@app.route("/api/enroll-voice", methods=["GET"])
def api_enroll_status():
    """Return enrollment status and profile info."""
    try:
        import voice_enrollor as ve
        enrolled = ve.is_enrolled()
        profile  = ve.load_profile() if enrolled else []
        with _enrollment_lock:
            session_active = _enrollment_session is not None and not _enrollment_session.done
            collected = _enrollment_session.collected if session_active else 0
            needed    = _enrollment_session.needed    if session_active else ve.NUM_SAMPLES
        return jsonify({
            "enrolled":       enrolled,
            "sample_count":   len(profile),
            "session_active": session_active,
            "collected":      collected,
            "needed":         needed,
            "threshold":      getattr(config, "VOICE_SIMILARITY_THRESHOLD", 0.62),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/enroll-voice/start", methods=["POST"])
def api_enroll_start():
    """Start a new enrollment session."""
    global _enrollment_session
    try:
        import voice_enrollor as ve
        wake_phrase = (getattr(config, "WAKE_WORD", "") or "").strip()
        if not wake_phrase:
            return jsonify({"ok": False, "error": "No wake phrase configured"}), 400
        with _enrollment_lock:
            _enrollment_session = ve.EnrollmentSession(wake_phrase)
            _enrollment_session.start()
        return jsonify({
            "ok": True,
            "phrase": wake_phrase,
            "needed": _enrollment_session.needed,
            "message": f"Say '{wake_phrase}' {_enrollment_session.needed} times. Trigger with your hotkey each time.",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/enroll-voice/feed", methods=["POST"])
def api_enroll_feed():
    """Feed a recorded WAV sample to the active enrollment session.
    POST: multipart/form-data with file='audio.wav'  OR  JSON {wav_b64: base64string}."""
    try:
        import base64

        with _enrollment_lock:
            if _enrollment_session is None or _enrollment_session.done:
                return jsonify({"ok": False, "error": "No active enrollment session"}), 400

        if "file" in request.files:
            wav_bytes = request.files["file"].read()
        else:
            data = request.get_json(force=True, silent=True) or {}
            b64  = data.get("wav_b64", "")
            if not b64:
                return jsonify({"ok": False, "error": "No audio provided"}), 400
            wav_bytes = base64.b64decode(b64)

        with _enrollment_lock:
            accepted, msg = _enrollment_session.feed_clip(wav_bytes)
            done      = _enrollment_session.done
            collected = _enrollment_session.collected
            needed    = _enrollment_session.needed

        # Enrollment just completed → turn speaker verification on so the freshly
        # enrolled profile is actually enforced (the flag gates verify in main.py).
        if done:
            config.VOICE_ENROLLMENT_ENABLED = True
            config.save()

        return jsonify({
            "ok":       True,
            "accepted": accepted,
            "message":  msg,
            "done":     done,
            "collected": collected,
            "needed":    needed,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/enroll-voice/clear", methods=["POST"])
def api_enroll_clear():
    """Delete the enrolled voice profile."""
    global _enrollment_session
    try:
        import voice_enrollor as ve
        ve.clear_profile()
        # Profile deleted → disable verification so the wake word keeps working
        # (verify would otherwise no-op, but keep the flag truthful for the UI).
        config.VOICE_ENROLLMENT_ENABLED = False
        config.save()
        with _enrollment_lock:
            _enrollment_session = None
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/calibrate-vad", methods=["POST"])
def api_calibrate_vad():
    """Set WAKE_WORD_VAD_THRESHOLD 20% below the current peak mic level.
    Call this while speaking the wake phrase to auto-calibrate.
    (Legacy single-shot endpoint — the dashboard now uses /api/calibrate-vad/guided.)"""
    rms = _mic_level
    if rms < 0.01:
        return jsonify({"ok": False, "error": "No audio detected — speak first"}), 400
    _thr_max = getattr(config, "WAKE_WORD_VAD_THRESHOLD_MAX", 0.30)
    new_thr = round(min(_thr_max, max(0.01, rms * 0.80)), 3)
    config.WAKE_WORD_VAD_THRESHOLD = new_thr
    config.WAKE_AUTO_RAISE_ENABLED = False   # calibrated = user-chosen; don't overwrite
    config.save()
    return jsonify({"ok": True, "threshold": new_thr, "voice_rms": round(rms, 4)})


def _percentile(values, pct: float) -> float:
    """Nearest-rank percentile of a list (no numpy dependency at request time)."""
    vals = sorted(values)
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round(pct / 100.0 * (len(vals) - 1)))))
    return float(vals[idx])


def _guided_threshold(ambient: list, speech: list):
    """Compute a wake threshold from two mic-level sample sets.

    ambient — RMS samples taken while the user stayed quiet.
    speech  — RMS samples taken while the user read the calibration sentence.

    Returns (threshold, stats_dict) or (None, error_string).

    The gate compares a fast-attack EMA of block RMS against the threshold, so
    the threshold must sit clearly ABOVE the ambient floor (or the gate fires
    on room noise) and clearly BELOW typical speech level (or the wake phrase
    can't cross it). Speech samples include inter-word gaps, so the 75th
    percentile — not the peak — represents what the EMA sees while talking.
    """
    ambient = [float(v) for v in ambient if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0]
    speech  = [float(v) for v in speech  if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0]
    if len(ambient) < 5 or len(speech) < 10:
        return None, "Not enough audio captured — try again"

    ambient_floor = _percentile(ambient, 90)
    speech_level  = _percentile(speech, 75)

    if speech_level < 0.01:
        return None, "No voice detected — read the sentence out loud during step 2"
    if speech_level < ambient_floor * 1.4:
        return None, (f"Your voice ({speech_level:.3f} RMS) is too close to the background "
                      f"noise ({ambient_floor:.3f} RMS). Move somewhere quieter or closer "
                      f"to the mic, then calibrate again.")

    # Sit above the ambient floor with margin, but never above 65% of speech
    # level — the wake phrase must clear the gate comfortably every time.
    thr = max(ambient_floor * 1.4, speech_level * 0.45)
    thr = min(thr, speech_level * 0.65)
    _thr_max = getattr(config, "WAKE_WORD_VAD_THRESHOLD_MAX", 0.30)
    thr = round(max(0.01, min(_thr_max, thr)), 3)
    return thr, {"ambient_floor": round(ambient_floor, 4),
                 "speech_level":  round(speech_level, 4)}


@app.route("/api/calibrate-vad/sample", methods=["POST"])
def api_calibrate_vad_sample():
    """Sample the live mic level server-side for {ms} milliseconds and return
    the samples. One blocking request per calibration phase — far more robust
    than the page polling /api/mic-level every 100 ms (rapid-fire fetches
    proved flaky inside the pywebview desktop window). Flask's dev server
    runs threaded, so the block doesn't stall other requests."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        ms = int(data.get("ms", 3000))
    except (TypeError, ValueError):
        ms = 3000
    ms = max(500, min(10000, ms))
    samples = []
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        samples.append(float(_mic_level))
        time.sleep(0.05)
    if all(v == 0.0 for v in samples):
        return jsonify({"ok": False, "error":
                        "Mic isn't being monitored — make sure the main NibCast app "
                        "is running (not just the dashboard) and voice activation is on."}), 400
    return jsonify({"ok": True, "samples": samples})


@app.route("/api/calibrate-vad/guided", methods=["POST"])
def api_calibrate_vad_guided():
    """Guided calibration: the dashboard collects mic-level samples during a
    quiet phase and a read-this-sentence phase, then posts both lists here.
    Computes a threshold between the ambient floor and the user's speech level,
    saves it, and disables the ambient auto-raise (a calibrated value is a
    user-chosen value)."""
    data = request.get_json(force=True, silent=True) or {}
    thr, extra = _guided_threshold(data.get("ambient") or [], data.get("speech") or [])
    if thr is None:
        return jsonify({"ok": False, "error": extra}), 400
    config.WAKE_WORD_VAD_THRESHOLD = thr
    config.WAKE_AUTO_RAISE_ENABLED = False
    config.save()
    log.info(f"Guided calibration: threshold → {thr} "
             f"(ambient {extra['ambient_floor']}, speech {extra['speech_level']}); "
             f"auto-raise disabled")
    return jsonify({"ok": True, "threshold": thr, **extra})


_inject_callback = None


def set_inject_callback(cb):
    global _inject_callback
    _inject_callback = cb


@app.route("/api/inject", methods=["POST"])
def api_inject():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "no text"}), 400
    if _inject_callback is None:
        return jsonify({"ok": False, "error": "injector not ready"}), 503
    try:
        _inject_callback(text)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/widget-style", methods=["POST"])
def api_widget_style():
    data  = request.get_json(force=True, silent=True) or {}
    style = data.get("style", "wave")
    if style in ("wave", "orbit", "pulse"):
        config.WIDGET_STYLE = style
        config.save()
        if _widget_ref:
            try:
                _widget_ref.set_icon_style(style)
            except Exception:
                pass
    return jsonify({"ok": True, "style": style})


@app.route("/api/create-shortcut", methods=["POST"])
def api_create_shortcut():
    """Create a desktop shortcut pointing to NibCast.exe (frozen) or pythonw main.py."""
    try:
        import sys
        import subprocess as _sp
        import os as _os

        # Detect if running as a PyInstaller bundle
        if getattr(sys, "frozen", False):
            target = sys.executable          # NibCast.exe itself
            icon_src  = sys.executable
        else:
            import install as _inst
            _inst.create_desktop_shortcut()
            return jsonify({"ok": True})

        # Build desktop path (handles OneDrive-redirected Desktop)
        ps_desktop = _sp.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True,
        )
        desktop  = ps_desktop.stdout.strip() or _os.path.join(_os.path.expanduser("~"), "Desktop")
        shortcut = _os.path.join(desktop, "NibCast.lnk")
        work_dir = _os.path.dirname(target)

        # Escape single quotes so paths like C:\Users\O'Brien\... don't break the
        # PowerShell string literals — replace ' with '' (PS escape for a literal quote).
        def _ps(p: str) -> str: return p.replace("'", "''")
        ps_script = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{_ps(shortcut)}')
$lnk.TargetPath       = '{_ps(target)}'
$lnk.WorkingDirectory = '{_ps(work_dir)}'
$lnk.Description      = 'NibCast - AI Voice Dictation'
$lnk.IconLocation     = '{_ps(icon_src)}'
$lnk.Save()
"""
        result = _sp.run(["powershell", "-NoProfile", "-Command", ps_script],
                         capture_output=True, text=True)
        if result.returncode == 0:
            return jsonify({"ok": True, "path": shortcut})
        return jsonify({"ok": False, "error": result.stderr.strip()}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/widget-shape", methods=["POST"])
def api_widget_shape():
    data  = request.get_json(force=True, silent=True) or {}
    shape = data.get("shape", "orb")
    if shape in ("orb", "bar", "chip"):
        config.WIDGET_SHAPE = shape
        config.save()
        if _widget_ref:
            try:
                _widget_ref.set_widget_shape(shape)
            except Exception:
                pass
    return jsonify({"ok": True, "shape": shape})


@app.route("/")
def index():
    return render_template("dashboard.html")


# ────────────────────────────────────────────────────────────
# Server
# ────────────────────────────────────────────────────────────
def start_dashboard():
    import logging as _log
    _log.getLogger("werkzeug").setLevel(_log.ERROR)

    if not auth.is_configured():
        log.info("🔓 Dashboard PIN not yet set — first visit will land on /setup.")

    def _run():
        try:
            app.run(host="127.0.0.1", port=DASHBOARD_PORT, debug=False, use_reloader=False)
        except OSError as e:
            log.error(f"❌ Dashboard failed to bind port {DASHBOARD_PORT}: {e} "
                      f"— another NibCast instance may already be running.")
        except SystemExit as e:
            # Werkzeug's single-threaded dev server runs request handlers on this
            # same thread/loop — an uncaught sys.exit() anywhere in a route handler
            # (e.g. a bad `from main import ...` re-executing main.py and hitting
            # its single-instance guard) would otherwise propagate here and kill
            # the dashboard for the rest of the process's life, with no way to
            # reopen it short of restarting NibCast entirely.
            log.error(f"❌ Dashboard server exited unexpectedly ({e}) — "
                      f"the dashboard is no longer reachable until NibCast restarts.")

    t = threading.Thread(target=_run, daemon=True, name="DashboardThread")
    t.start()
    log.info(f"🌐 Dashboard → http://localhost:{DASHBOARD_PORT}")
    return t


if __name__ == "__main__":
    # Standalone dashboard — no hotkeys/audio pipeline. Useful for UI work.
    db.init_db()
    thread = start_dashboard()
    thread.join()
