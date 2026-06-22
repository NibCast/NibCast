# ============================================================
#  NibCast — Authentication
# ============================================================
#  Three auth methods (any combination can be enabled):
#   1. PIN      — text password, PBKDF2-hashed
#   2. Pattern  — Android-style 3×3 grid; stored as hashed sequence
#   3. TOTP     — Google Authenticator / Authy (pyotp, RFC 6238)
# ============================================================

import os
import json
import hashlib
import hmac
import secrets
import threading
import time

from config import USER_DIR

_LEGACY_DIR  = os.path.dirname(os.path.abspath(__file__))
_LEGACY_AUTH = os.path.join(_LEGACY_DIR, ".vf_auth")

_AUTH_FILE   = os.path.join(USER_DIR, ".vf_auth")
_SALT_FILE   = os.path.join(USER_DIR, ".vf_salt")
_TOTP_FILE   = os.path.join(USER_DIR, ".vf_totp")
_PATTERN_FILE= os.path.join(USER_DIR, ".vf_pattern")


# ── Salt ─────────────────────────────────────────────────────
# Lock prevents two threads from racing to create the salt file on first run.

_salt_lock = threading.Lock()

def _get_salt() -> str:
    with _salt_lock:
        if not os.path.exists(_SALT_FILE):
            salt = secrets.token_hex(32)
            try:
                with open(_SALT_FILE, "w", encoding="utf-8") as f:
                    f.write(salt)
            except Exception:
                return "nibcast_fallback_salt"
            return salt
        try:
            with open(_SALT_FILE, "r", encoding="utf-8") as f:
                return f.read().strip() or "nibcast_fallback_salt"
        except Exception:
            return "nibcast_fallback_salt"


# ── Migration ────────────────────────────────────────────────

def _migrate_legacy():
    if os.path.exists(_LEGACY_AUTH) and not os.path.exists(_AUTH_FILE):
        try:
            with open(_LEGACY_AUTH, "r", encoding="utf-8") as src, \
                 open(_AUTH_FILE,   "w", encoding="utf-8") as dst:
                dst.write(src.read())
        except Exception:
            pass

_migrate_legacy()


# ── Flask secret ─────────────────────────────────────────────
# Stored as a standalone random 64-byte hex in its own file so it cannot be
# derived by reading the salt file — forging a session cookie requires this file.

_SECRET_FILE = os.path.join(USER_DIR, ".vf_secret")

def get_flask_secret() -> str:
    if os.path.exists(_SECRET_FILE):
        try:
            with open(_SECRET_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip()
            if len(val) >= 32:
                return val
        except Exception:
            pass
    secret = secrets.token_hex(32)
    try:
        with open(_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(secret)
    except Exception:
        pass
    return secret


# ──────────────────────────────────────────────────────────────
# 1. PIN AUTH
# ──────────────────────────────────────────────────────────────

def _hash_pin(pin: str) -> str:
    salt = _get_salt()
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return dk.hex()

def _legacy_hash_pin(pin: str) -> str:
    return hashlib.sha256(f"nibcast_v1:{pin}".encode()).hexdigest()

def is_configured() -> bool:
    return os.path.exists(_AUTH_FILE)

def setup_pin(pin: str) -> None:
    with open(_AUTH_FILE, "w", encoding="utf-8") as f:
        f.write(_hash_pin(pin))

def verify_pin(pin: str) -> bool:
    if not is_configured():
        return True
    try:
        with open(_AUTH_FILE, "r", encoding="utf-8") as f:
            stored = f.read().strip()
    except Exception:
        return False

    if hmac.compare_digest(stored, _hash_pin(pin)):
        return True

    # One-time migration from legacy hash
    if hmac.compare_digest(stored, _legacy_hash_pin(pin)):
        try:
            setup_pin(pin)
        except Exception:
            pass
        return True
    return False

def change_pin(old_pin: str, new_pin: str) -> bool:
    if not verify_pin(old_pin):
        return False
    setup_pin(new_pin)
    return True


# ──────────────────────────────────────────────────────────────
# 2. PATTERN LOCK  (3×3 dot grid)
# ──────────────────────────────────────────────────────────────
# Stored as a PBKDF2 hash of the comma-joined sequence, e.g. "1,5,9,8,7"

def is_pattern_configured() -> bool:
    return os.path.exists(_PATTERN_FILE)

def setup_pattern(sequence: str) -> None:
    """sequence = comma-separated dot numbers, e.g. '1,5,9,8,7'"""
    clean = _normalise_pattern(sequence)
    if not clean:
        raise ValueError("Pattern must have at least 4 dots")
    salt = _get_salt()
    dk = hashlib.pbkdf2_hmac("sha256", clean.encode(), salt.encode(), 100_000)
    with open(_PATTERN_FILE, "w", encoding="utf-8") as f:
        f.write(dk.hex())

def verify_pattern(sequence: str) -> bool:
    if not is_pattern_configured():
        return False
    clean = _normalise_pattern(sequence)
    salt  = _get_salt()
    dk    = hashlib.pbkdf2_hmac("sha256", clean.encode(), salt.encode(), 100_000)
    try:
        with open(_PATTERN_FILE, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        return hmac.compare_digest(stored, dk.hex())
    except Exception:
        return False

def _normalise_pattern(seq: str) -> str:
    """Accept '1,5,9' or '159' — return canonical '1,5,9'."""
    seq = seq.strip()
    if "," in seq:
        parts = [p.strip() for p in seq.split(",") if p.strip().isdigit()]
    else:
        parts = list(seq)  # each char is a digit
    parts = [p for p in parts if p in "123456789"]
    return ",".join(parts)


# ──────────────────────────────────────────────────────────────
# 3. TOTP  (RFC 6238 — Google Authenticator compatible)
# ──────────────────────────────────────────────────────────────

def _totp_data() -> dict:
    if not os.path.exists(_TOTP_FILE):
        return {}
    try:
        with open(_TOTP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def is_totp_configured() -> bool:
    return bool(_totp_data().get("enabled") and _totp_data().get("secret"))

def setup_totp() -> dict:
    """Generate a new TOTP secret. Returns {secret, uri, enabled}."""
    try:
        import pyotp
    except ImportError:
        raise RuntimeError("pyotp not installed — run: pip install pyotp")

    secret = pyotp.random_base32()
    totp   = pyotp.TOTP(secret)
    uri    = totp.provisioning_uri(name="NibCast", issuer_name="NibCast")
    data   = {"secret": secret, "enabled": False, "uri": uri}
    with open(_TOTP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return {"secret": secret, "uri": uri}

def confirm_totp(code: str) -> bool:
    """Verify once to confirm setup, then mark as enabled."""
    data = _totp_data()
    if not data.get("secret"):
        return False
    try:
        import pyotp
        totp = pyotp.TOTP(data["secret"])
        if totp.verify(code, valid_window=1):
            data["enabled"] = True
            with open(_TOTP_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return True
    except Exception:
        pass
    return False

def verify_totp(code: str) -> bool:
    data = _totp_data()
    if not data.get("enabled") or not data.get("secret"):
        return False
    try:
        import pyotp
        totp = pyotp.TOTP(data["secret"])
        return totp.verify(code, valid_window=1)
    except Exception:
        return False

def disable_totp() -> None:
    data = _totp_data()
    data["enabled"] = False
    with open(_TOTP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ──────────────────────────────────────────────────────────────
# Session tokens (unchanged)
# ──────────────────────────────────────────────────────────────

SESSION_TTL = 4 * 3600
_SESSIONS: dict = {}

def create_session() -> str:
    token = secrets.token_hex(32)
    _SESSIONS[token] = time.time() + SESSION_TTL
    _purge_expired()
    return token

def check_session(token: str) -> bool:
    expiry = _SESSIONS.get(token)
    if expiry is None: return False
    if time.time() > expiry:
        _SESSIONS.pop(token, None)
        return False
    return True

def revoke_session(token: str) -> None:
    _SESSIONS.pop(token, None)

def _purge_expired():
    now = time.time()
    for k in [k for k, v in _SESSIONS.items() if v < now]:
        _SESSIONS.pop(k, None)
