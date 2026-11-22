# ============================================================
#  NibCast — Hybrid race: two engines, first good answer wins
# ============================================================
#  Brain / Hybrid Mode runs two engines on the same input (e.g.
#  cloud Groq + a local model) and keeps the better result.
#  Waiting for BOTH made every paste as slow as the slower engine
#  — a local 7B model on CPU can take several seconds longer than
#  Groq. Instead: as soon as one engine returns a usable result,
#  give the other a short grace window to finish, then judge
#  whatever has arrived.
# ============================================================

from __future__ import annotations

import threading
import time


def race_two(fn_a, fn_b, timeout: float, grace: float, usable) -> tuple:
    """Run fn_a and fn_b in parallel. Returns (result_a, result_b); a result is
    None if that engine didn't finish in time.

    Waits until both finish, or until one has returned a result for which
    usable(result) is True plus `grace` seconds, or `timeout` overall."""
    results = [None, None]
    done = [threading.Event(), threading.Event()]
    cond = threading.Condition()

    def run(i, fn):
        try:
            results[i] = fn()
        except Exception:
            results[i] = ""
        finally:
            done[i].set()
            with cond:
                cond.notify_all()

    for i, fn in enumerate((fn_a, fn_b)):
        threading.Thread(target=run, args=(i, fn), daemon=True, name=f"Hybrid{i}").start()

    deadline = time.time() + timeout
    good_at = None
    with cond:
        while not (done[0].is_set() and done[1].is_set()):
            now = time.time()
            if good_at is None:
                for i in (0, 1):
                    if done[i].is_set() and usable(results[i]):
                        good_at = now
                        break
            limit = deadline if good_at is None else min(deadline, good_at + grace)
            if now >= limit:
                break
            cond.wait(timeout=min(0.05, limit - now))
    return (results[0] if done[0].is_set() else None,
            results[1] if done[1].is_set() else None)


def race_many(fns, timeout: float, grace: float, usable) -> list:
    """race_two for N engines. Runs every fn in parallel and returns a list of
    (result, seconds) per engine — result is None when that engine hadn't
    finished. Returns when all are done, or `grace` seconds after the first
    usable result, or at `timeout` overall."""
    n = len(fns)
    results = [None] * n
    elapsed = [None] * n
    done = [threading.Event() for _ in range(n)]
    cond = threading.Condition()
    t0 = time.time()

    def run(i, fn):
        try:
            results[i] = fn()
        except Exception:
            results[i] = ""
        finally:
            elapsed[i] = time.time() - t0
            done[i].set()
            with cond:
                cond.notify_all()

    for i, fn in enumerate(fns):
        threading.Thread(target=run, args=(i, fn), daemon=True, name=f"Auto{i}").start()

    deadline = t0 + timeout
    good_at = None
    with cond:
        while not all(d.is_set() for d in done):
            now = time.time()
            if good_at is None and any(done[i].is_set() and usable(results[i]) for i in range(n)):
                good_at = now
            limit = deadline if good_at is None else min(deadline, good_at + grace)
            if now >= limit:
                break
            cond.wait(timeout=min(0.05, max(0.0, limit - now)))
    return [(results[i], elapsed[i]) if done[i].is_set() else (None, None) for i in range(n)]
