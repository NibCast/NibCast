# ============================================================
#  NibCast — Logger
# ============================================================
import logging
import os

LOG_DIR  = os.path.join(os.path.expanduser("~"), ".nibcast")
LOG_FILE = os.path.join(LOG_DIR, "nibcast.log")
os.makedirs(LOG_DIR, exist_ok=True)

# Set console output to UTF-8 on Windows to support emojis
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleOutputCP(65001)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("NibCast")


def redact(text) -> str:
    """Transcript text for a log line, or a placeholder when Privacy Mode is on.
    Use for every log message that would otherwise contain dictated words."""
    try:
        import config
        private = getattr(config, "PRIVACY_MODE", False)
    except Exception:
        private = False
    if private:
        return f"<{len(text or '')} chars hidden — privacy mode>"
    return repr(text)
