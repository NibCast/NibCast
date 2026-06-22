# ============================================================
#  NibCast — Voice Enrollment & Speaker Verification
# ============================================================
#  Records 3 samples of the user saying the wake phrase,
#  builds a lightweight speaker profile from spectral features,
#  and verifies each Phase-1 clip against that profile before
#  arming Phase 2.  No heavy ML dependencies — numpy only.
#
#  Features used:
#    - Spectral band-energy ratios (6 bands, 0-8 kHz)
#    - Zero-crossing rate (correlates with voiced speech quality)
#    - RMS level and peak level (loudness profile)
#    - Duration (speaking pace)
#
#  This is not cryptographic speaker ID — it primarily rejects
#  clips that are acoustically very different from the enrolled
#  voice (e.g., background TV, different speaker, wrong phrase).
# ============================================================

import io
import os
import json
import wave
import time
import numpy as np

from config import USER_DIR
from logger import log

PROFILE_PATH   = os.path.join(USER_DIR, "voice_profile.json")
SAMPLES_DIR    = os.path.join(USER_DIR, "wake_samples")
NUM_SAMPLES    = 3       # recordings required for enrollment
SIMILARITY_THR = 0.62    # below this → reject (tune via dashboard)

os.makedirs(SAMPLES_DIR, exist_ok=True)


# ── Feature extraction ────────────────────────────────────────

def _pcm_to_float(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Return (float32 samples, sample_rate) from WAV bytes."""
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        frames = wf.readframes(wf.getnframes())
        rate   = wf.getframerate()
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


def extract_features(wav_bytes: bytes) -> dict:
    """Extract lightweight speaker features from WAV audio."""
    try:
        samples, rate = _pcm_to_float(wav_bytes)
    except Exception:
        return {}

    if len(samples) < 160:
        return {}

    rms      = float(np.sqrt(np.mean(samples ** 2)))
    dur      = len(samples) / max(rate, 1)
    zcr      = float(np.sum(np.abs(np.diff(np.sign(samples)))) / (2 * len(samples)))

    # Per-chunk peak (energy envelope)
    chunk_n  = max(1, len(samples) // 20)
    chunks   = [samples[i:i+chunk_n] for i in range(0, len(samples), chunk_n) if len(samples[i:i+chunk_n]) > 0]
    peak_rms = float(max(np.sqrt(np.mean(c**2)) for c in chunks)) if chunks else rms

    # FFT spectral band energies
    fft   = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(len(samples), d=1.0/rate)
    bands = [(0,200),(200,500),(500,1000),(1000,2000),(2000,4000),(4000,8000)]
    band_e = []
    for lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi)
        band_e.append(float(np.sum(fft[mask]**2)) if mask.any() else 0.0)
    total_e    = sum(band_e) or 1.0
    band_ratios = [e / total_e for e in band_e]

    return {
        "rms":         rms,
        "peak_rms":    peak_rms,
        "duration":    dur,
        "zcr":         zcr,
        "band_ratios": band_ratios,
    }


def _cosine(a: list, b: list) -> float:
    a, b   = np.array(a, dtype=float), np.array(b, dtype=float)
    denom  = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def similarity_score(probe: dict, profile_samples: list[dict]) -> float:
    """
    Compare probe features against enrolled profile samples.
    Returns similarity 0–1 (higher = more likely same speaker).
    Returns 1.0 if no profile (always accept when not enrolled).
    """
    if not profile_samples or not probe:
        return 1.0

    scores = []
    for ref in profile_samples:
        if not ref or "band_ratios" not in ref:
            continue

        # Spectral shape — most speaker-characteristic (50% weight)
        band_sim = _cosine(probe.get("band_ratios", [0]*6), ref["band_ratios"])

        # Zero-crossing rate — voiced quality (20% weight)
        zcr_diff = abs(probe.get("zcr", 0) - ref.get("zcr", 0))
        zcr_sim  = max(0.0, 1.0 - zcr_diff * 15)

        # Duration — speaking pace (15% weight); allow ±60% variation
        dur_ratio = probe.get("duration", 1.0) / max(ref.get("duration", 0.01), 0.01)
        dur_sim   = max(0.0, 1.0 - abs(1.0 - dur_ratio) * 0.8)

        # Loudness match (15% weight); allow ±50% variation
        rms_ratio = probe.get("rms", 0) / max(ref.get("rms", 0.001), 0.001)
        rms_sim   = max(0.0, 1.0 - abs(1.0 - rms_ratio) * 0.7)

        scores.append(0.50*band_sim + 0.20*zcr_sim + 0.15*dur_sim + 0.15*rms_sim)

    return max(scores) if scores else 1.0


# ── Profile persistence ───────────────────────────────────────

def load_profile() -> list[dict]:
    """Load enrolled voice profile. Returns [] if none."""
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("samples", [])
    except Exception:
        return []


def save_profile(samples: list[dict]):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump({"samples": samples, "enrolled_at": time.time()}, f, indent=2)
    log.info(f"Voice profile saved ({len(samples)} samples)")


def clear_profile():
    try:
        os.remove(PROFILE_PATH)
    except FileNotFoundError:
        pass
    log.info("Voice profile cleared")


def is_enrolled() -> bool:
    return os.path.exists(PROFILE_PATH) and bool(load_profile())


# ── Live enrollment session ───────────────────────────────────

class EnrollmentSession:
    """
    Guides the user through recording NUM_SAMPLES utterances of
    the wake phrase.  Integrate with the audio recorder:

        session = EnrollmentSession(wake_phrase)
        session.start(on_complete=lambda profile: ...)

    Each call to session.feed_clip(wav_bytes) tries to accept one
    sample.  When all samples are collected, on_complete is called
    with the list of feature dicts.
    """

    def __init__(self, wake_phrase: str, num_samples: int = NUM_SAMPLES):
        self._phrase     = wake_phrase
        self._n          = num_samples
        self._samples    = []
        self._on_complete = None
        self._done       = False

    @property
    def collected(self) -> int:
        return len(self._samples)

    @property
    def needed(self) -> int:
        return self._n

    @property
    def done(self) -> bool:
        return self._done

    def start(self, on_complete=None):
        self._on_complete = on_complete
        log.info(f"Enrollment: say '{self._phrase}' {self._n} times")

    def feed_clip(self, wav_bytes: bytes) -> tuple[bool, str]:
        """
        Try to accept this clip as an enrollment sample.
        Returns (accepted: bool, message: str).
        """
        if self._done:
            return False, "Already complete"

        features = extract_features(wav_bytes)
        if not features:
            return False, "Audio too short or silent"

        if features["rms"] < 0.01:
            return False, "Too quiet — speak closer to the microphone"

        self._samples.append(features)
        n = len(self._samples)
        log.info(f"Enrollment sample {n}/{self._n}: rms={features['rms']:.3f} dur={features['duration']:.2f}s")

        if n >= self._n:
            self._done = True
            save_profile(self._samples)
            if self._on_complete:
                self._on_complete(self._samples)
            return True, f"Enrolled! {n}/{self._n} samples saved."

        return True, f"Sample {n}/{self._n} recorded. Say it again."


# ── Verify helper (called from main.py Phase 1 check) ─────────

_profile_cache: list[dict] | None = None
_profile_mtime: float = 0.0


def verify(wav_bytes: bytes, threshold: float = SIMILARITY_THR) -> tuple[bool, float]:
    """
    Verify that wav_bytes sounds like the enrolled speaker.
    Returns (accepted: bool, score: float).
    If no profile is enrolled, always returns (True, 1.0).
    """
    global _profile_cache, _profile_mtime

    # Re-load profile only if the file changed
    try:
        mtime = os.path.getmtime(PROFILE_PATH)
    except FileNotFoundError:
        return True, 1.0   # not enrolled → always accept

    if _profile_cache is None or mtime != _profile_mtime:
        _profile_cache = load_profile()
        _profile_mtime = mtime

    if not _profile_cache:
        return True, 1.0

    probe = extract_features(wav_bytes)
    score = similarity_score(probe, _profile_cache)
    accepted = score >= threshold
    if not accepted:
        log.info(f"Speaker verify: REJECTED score={score:.2f} < threshold={threshold:.2f}")
    else:
        log.debug(f"Speaker verify: OK score={score:.2f}")
    return accepted, score
