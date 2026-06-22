# ============================================================
#  NibCast — Audio cues
# ============================================================
import threading
import numpy as np

import config
from logger import log

try:
    import sounddevice as sd
    _HAS_SD = True
except Exception:
    _HAS_SD = False


def _tone(freq: float, dur: float = 0.08, sr: int = 22050,
          fade: float = 0.02, amp: float = 0.18) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    wave = amp * np.sin(2 * np.pi * freq * t)
    f = int(sr * fade)
    if f > 0 and 2 * f < n:
        wave[:f]  *= np.linspace(0, 1, f)
        wave[-f:] *= np.linspace(1, 0, f)
    return wave.astype(np.float32)


def _play(samples: np.ndarray):
    if not _HAS_SD:
        return
    try:
        sd.play(samples, samplerate=22050, blocking=False)
    except Exception as e:
        log.debug(f"notifier play error: {e}")


def _fire(samples: np.ndarray):
    threading.Thread(target=_play, args=(samples,), daemon=True).start()


_START = np.concatenate([_tone(880, 0.07), _tone(1320, 0.09)])
_STOP  = np.concatenate([_tone(1320, 0.07), _tone(660, 0.09)])
_ERROR = _tone(220, 0.18, fade=0.04, amp=0.25)


def ding_start():
    if getattr(config, "AUDIO_CUE_START", True) and getattr(config, "AUDIO_CUES", True):
        _fire(_START)


def ding_stop():
    if getattr(config, "AUDIO_CUE_STOP", True) and getattr(config, "AUDIO_CUES", True):
        _fire(_STOP)


def ding_error():
    if getattr(config, "AUDIO_CUE_ERROR", True) and getattr(config, "AUDIO_CUES", True):
        _fire(_ERROR)
