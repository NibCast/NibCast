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


# Recent problems (WARNING and above) kept in memory for the dashboard's
# System Health panel, so the user can see what went wrong without opening the
# log file. Messages are the same as in the log (transcripts already redacted).
import collections as _collections
import time as _time

RECENT_PROBLEMS = _collections.deque(maxlen=40)


class _ProblemBuffer(logging.Handler):
    def emit(self, record):
        try:
            RECENT_PROBLEMS.append({"time": _time.time(), "level": record.levelname,
                                    "message": record.getMessage()[:300]})
        except Exception:
            pass


_problem_handler = _ProblemBuffer(level=logging.WARNING)
log.addHandler(_problem_handler)


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
