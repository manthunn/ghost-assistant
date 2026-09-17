"""Pause an actively playing Windows media session during user speech.

Uses the existing microphone stream and Windows media-session state. Explicit
pause/play requests avoid turning already-paused music on when speech starts.
Apps that do not expose a Windows media session are left alone.
"""
import asyncio
import threading
import time

SPEECH_RMS = 300
SPEECH_ON = 0.25
SPEECH_OFF = 1.2
POLL = 0.05
API_TIMEOUT = 3


class MicActivity:
    """The microphone callback writes loudness; the watcher reads it."""

    def __init__(self):
        self._loud = False
        self._lock = threading.Lock()

    def set(self, loud):
        with self._lock:
            self._loud = loud

    def is_loud(self):
        with self._lock:
            return self._loud


class MediaPauser:
    def __init__(self, manager, statuses):
        self.manager = manager
        self.statuses = statuses
        self.paused = None

    async def pause(self):
        if self.paused is not None:
            return
        session = self.manager.get_current_session()
        if session is None:
            return
        info = session.get_playback_info()
        if info.playback_status != self.statuses.PLAYING or not info.controls.is_pause_enabled:
            return
        if await asyncio.wait_for(session.try_pause_async(), API_TIMEOUT):
            self.paused = session

    async def resume(self):
        session, self.paused = self.paused, None
        if session is None:
            return
        current = self.manager.get_current_session()
        # Respect a switch to another app while Ghost was listening.
        if current is None or current.source_app_user_model_id != session.source_app_user_model_id:
            return
        info = session.get_playback_info()
        if info.playback_status == self.statuses.PAUSED and info.controls.is_play_enabled:
            await asyncio.wait_for(session.try_play_async(), API_TIMEOUT)


async def _watch(activity, stop_event, pauser):
    speaking, handled = False, False
    since = time.monotonic()
    try:
        while not stop_event.is_set():
            now, loud = time.monotonic(), activity.is_loud()
            if loud != speaking:
                speaking, since = loud, now
            elif speaking and not handled and now - since >= SPEECH_ON:
                await pauser.pause()
                handled = True
            elif not speaking and handled and now - since >= SPEECH_OFF:
                await pauser.resume()
                handled = False
            await asyncio.sleep(POLL)
    finally:
        await pauser.resume()


async def _watch_live(activity, stop_event):
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )
    manager = await asyncio.wait_for(Manager.request_async(), API_TIMEOUT)
    await _watch(activity, stop_event, MediaPauser(manager, Status))


def start_pause_watching(activity, stop_event):
    """Return a watcher thread that restores playback before exiting."""
    def loop():
        initialized = False
        try:
            from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
            init_apartment(ApartmentType.MULTI_THREADED)
            initialized = True
            asyncio.run(_watch_live(activity, stop_event))
        except Exception as e:
            print(f"  [media auto-pause unavailable] {e}")
        finally:
            if initialized:
                uninit_apartment()

    watcher = threading.Thread(target=loop, daemon=True)
    watcher.start()
    return watcher
