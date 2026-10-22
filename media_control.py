# ============================================================
#  NibCast — Pause music while you dictate
# ============================================================
#  Pauses whatever is PLAYING (Spotify, YouTube in a browser,
#  VLC, …) when dictation starts and resumes exactly those
#  players when it ends — so music doesn't bleed into the mic
#  and you don't have to pause it yourself.
#
#  Uses Windows' media-session API (the same one behind the
#  volume flyout's play/pause), so it never toggles a player
#  that wasn't playing — unlike sending the Play/Pause media key,
#  which would START music if nothing was playing.
#
#  Optional dependency (Windows 10+):   pip install winsdk
#  (the split "winrt-Windows.Media.Control" packages also work)
# ============================================================

from __future__ import annotations

import asyncio
import importlib
import queue
import sys
import threading

from logger import log

PLAYING, PAUSED = 4, 5    # GlobalSystemMediaTransportControlsSessionPlaybackStatus


def _load_api():
    """The media-control namespace from whichever WinRT binding is installed."""
    if sys.platform != "win32":
        return None
    for mod in ("winsdk.windows.media.control", "winrt.windows.media.control"):
        try:
            return importlib.import_module(mod)
        except Exception:
            continue
    return None


def available() -> bool:
    return _load_api() is not None


class _WinRTBackend:
    def __init__(self, api):
        self._mgr_cls = api.GlobalSystemMediaTransportControlsSessionManager

    async def _sessions(self):
        mgr = await self._mgr_cls.request_async()
        return list(mgr.get_sessions())

    async def pause_playing(self) -> list:
        paused = []
        for s in await self._sessions():
            try:
                if int(s.get_playback_info().playback_status) == PLAYING and await s.try_pause_async():
                    paused.append(s.source_app_user_model_id)
            except Exception:
                continue
        return paused

    async def resume(self, app_ids: list) -> int:
        n = 0
        for s in await self._sessions():
            try:
                # Only players WE paused, and only if they're still paused — if
                # the user stopped or changed track meanwhile, leave it alone.
                if (s.source_app_user_model_id in app_ids
                        and int(s.get_playback_info().playback_status) == PAUSED
                        and await s.try_play_async()):
                    n += 1
            except Exception:
                continue
        return n


_OP_TIMEOUT = 3.0     # a stuck media session must never wedge later pause/resume calls


class MediaPauser:
    """pause() at dictation start, resume() at the end. Both return immediately
    and are safe to call repeatedly. The OS calls run on ONE daemon worker in
    call order, so a slow pause can never land after its resume (which would
    leave the music paused); each call is time-limited so a hung session can't
    block the queue or keep the app from exiting."""

    def __init__(self, backend=None):
        self._backend = backend
        self._paused: list = []
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="MediaControl").start()

    def _worker(self):
        while True:
            fn = self._q.get()
            try:
                fn()
            except Exception as e:
                log.debug(f"Media control error: {e}")

    def _run(self, coro):
        return asyncio.run(asyncio.wait_for(coro, _OP_TIMEOUT))

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every queued pause/resume has run (tests, shutdown)."""
        done = threading.Event()
        self._q.put(done.set)
        return done.wait(timeout)

    def _get_backend(self):
        if self._backend is None:
            api = _load_api()
            self._backend = _WinRTBackend(api) if api else False
        return self._backend or None

    def pause(self):
        import config
        if not getattr(config, "PAUSE_MEDIA_WHILE_DICTATING", False):
            return
        backend = self._get_backend()
        if backend is None:
            return

        def run():
            ids = self._run(backend.pause_playing())
            if ids:
                self._paused.extend(i for i in ids if i not in self._paused)
                log.info(f"⏸️  Paused media while dictating: {', '.join(ids)}")
        self._q.put(run)

    def resume(self):
        backend = self._get_backend()
        if backend is None:
            return

        def run():
            ids, self._paused = self._paused, []
            if ids and self._run(backend.resume(ids)):
                log.info(f"▶️  Resumed {len(ids)} media player(s)")
        self._q.put(run)


pauser = MediaPauser()
