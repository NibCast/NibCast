# ============================================================
#  NibCast — Screen context (optional, off by default)
# ============================================================
#  Spell names right: when you say "reply to Priyanka about the
#  Kubernetes migration", the names on your screen are the best
#  hint for how to spell them. At dictation start this reads the
#  ACTIVE WINDOW only:
#    • its title (always), and
#    • its visible text via Windows' built-in on-device OCR
#      (Windows.Media.Ocr — no cloud; needs  pip install winsdk)
#  and extracts likely names / product terms / @handles. Those
#  terms bias transcription and tell the cleanup LLM the exact
#  spellings — never to insert a term you didn't say.
#
#  Privacy: the screen image never leaves the PC and is never
#  saved. Only the short term list is sent, alongside your audio,
#  to the ASR/LLM you already use (nothing leaves in Offline Mode).
# ============================================================

from __future__ import annotations

import asyncio
import io
import re
import sys
import threading
from collections import Counter

from logger import log

MAX_TERMS = 40

# Capitalised words that are UI chrome or ordinary sentence starts, not names.
_COMMON = frozenset("""
a an the and or but if then so of to in on at by for with from up down out over under
i you he she it we they me him her us them my your his its our their this that these those
is are was were be been being have has had do does did will would can could should may might
not no yes all any some each every more most other new old first last next back here there
what when where who why how which file edit view help window tools insert format home search
settings options open save close share reply forward send sent inbox draft drafts archive
delete new today yesterday tomorrow page tab tabs menu start sign log login account profile
untitled document documents chat chats message messages channel channels thread threads
add remove create update copy paste cut undo redo select find replace print zoom
monday tuesday wednesday thursday friday saturday sunday january february march april may
june july august september october november december am pm ok okay hi hello thanks
subject cc bcc fwd attachment attachments reply all
""".split())

_TOKEN = re.compile(r"[@#]?[A-Za-z][A-Za-z0-9'’+\-]*[A-Za-z0-9+]|[@#]?[A-Za-z]{2}")


def extract_terms(text: str, limit: int = MAX_TERMS) -> list:
    """Likely names / terms from on-screen text, most frequent first."""
    counts: Counter = Counter()
    first: dict = {}
    for i, m in enumerate(_TOKEN.finditer(text or "")):
        tok = m.group(0).strip("'’-")
        word = tok.lstrip("@#")
        if not 2 <= len(word) <= 32:
            continue
        handle   = tok[0] in "@#" and len(word) >= 2
        camel    = bool(re.search(r"[a-z][A-Z]", word))                 # NibCast, iPhone
        acronym  = word.isupper() and 2 <= len(word) <= 6 and word.isalpha()
        mixed    = bool(re.search(r"\d", word)) and bool(re.search(r"[A-Za-z]", word))  # GPT4o
        capital  = word[0].isupper() and len(word) >= 3
        if not (handle or camel or acronym or mixed or capital):
            continue
        if word.lower() in _COMMON and not (handle or camel):
            continue
        key = tok if handle else word
        counts[key] += 1
        first.setdefault(key, i)
    ranked = sorted(counts, key=lambda k: (-counts[k], first[k]))
    return ranked[:limit]


# ── Windows capture ───────────────────────────────────────────

def window_title() -> str:
    try:
        import target_manager
        return target_manager.active_window_title()
    except Exception:
        return ""


def _window_rect():
    """Physical-pixel bounds of the foreground window (DWM extended frame)."""
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        return None
    if rect.right - rect.left < 50 or rect.bottom - rect.top < 50:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _ocr_api():
    for prefix in ("winsdk", "winrt"):
        try:
            import importlib
            return (importlib.import_module(f"{prefix}.windows.media.ocr"),
                    importlib.import_module(f"{prefix}.windows.graphics.imaging"),
                    importlib.import_module(f"{prefix}.windows.storage.streams"))
        except Exception:
            continue
    return None


def ocr_available() -> bool:
    return sys.platform == "win32" and _ocr_api() is not None


async def _ocr_png(png: bytes) -> str:
    ocr, imaging, streams = _ocr_api()
    stream = streams.InMemoryRandomAccessStream()
    writer = streams.DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await imaging.BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = ocr.OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return result.text or ""


def window_text_ocr() -> str:
    """Visible text of the active window via on-device OCR, or "" if unavailable."""
    if not ocr_available():
        return ""
    try:
        from PIL import ImageGrab
        rect = _window_rect()
        if rect is None:
            return ""
        # all_screens: the rect is in virtual-desktop coordinates, so a window on
        # a second monitor (negative x, or right of the primary) is captured too.
        img = ImageGrab.grab(bbox=rect, all_screens=True)
        # The OCR engine has a max dimension (~10k px); screens are well under,
        # but downscale very large windows to keep it quick.
        if max(img.size) > 2600:
            img.thumbnail((2600, 2600))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return asyncio.run(_ocr_png(buf.getvalue()))
    except Exception as e:
        log.debug(f"Screen OCR failed: {e}")
        return ""


def capture() -> list:
    import config
    text = window_title()
    if getattr(config, "SCREEN_CONTEXT_OCR", True):
        text += "\n" + window_text_ocr()
    return extract_terms(text)


class Capture:
    """Start at dictation start; read the terms when transcription begins."""

    def __init__(self):
        self._terms: list = []
        self._done = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="ScreenContext").start()

    def _run(self):
        try:
            self._terms = capture()
        except Exception as e:
            log.debug(f"Screen context failed: {e}")
        finally:
            self._done.set()

    def terms(self, timeout: float = 0.6) -> list:
        self._done.wait(timeout)
        return list(self._terms)


def prompt_hint(terms: list) -> str:
    if not terms:
        return ""
    return ("Names and terms visible on the speaker's screen — if the speaker says one "
            "of these (or a sound-alike of it), use this exact spelling; never insert "
            "one they didn't say: " + ", ".join(terms))
