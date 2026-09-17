"""Playback-state regression checks without controlling real media."""
import asyncio
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from ghost import media_pause as media

STATUS = NS(PLAYING=4, PAUSED=5, STOPPED=3)


class Session:
    source_app_user_model_id = "test.player"

    def __init__(self, status):
        self.status = status
        self.try_pause_async = AsyncMock(side_effect=self.pause)
        self.try_play_async = AsyncMock(side_effect=self.play)

    def get_playback_info(self):
        return NS(playback_status=self.status,
                  controls=NS(is_pause_enabled=True, is_play_enabled=True))

    async def pause(self):
        self.status = STATUS.PAUSED
        return True

    async def play(self):
        self.status = STATUS.PLAYING
        return True


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_and_stopped_media_never_start(self):
        for status in (STATUS.PAUSED, STATUS.STOPPED):
            with self.subTest(status=status):
                session = Session(status)
                manager = Mock(get_current_session=Mock(return_value=session))
                pauser = media.MediaPauser(manager, STATUS)
                await pauser.pause()
                await pauser.resume()
                session.try_pause_async.assert_not_called()
                session.try_play_async.assert_not_called()

    async def test_only_successfully_paused_session_is_resumed_once(self):
        session = Session(STATUS.PLAYING)
        pauser = media.MediaPauser(Mock(get_current_session=lambda: session), STATUS)
        await pauser.pause()
        await pauser.pause()
        await pauser.resume()
        await pauser.resume()
        session.try_pause_async.assert_awaited_once()
        session.try_play_async.assert_awaited_once()

    async def test_rejected_pause_does_not_schedule_resume(self):
        session = Session(STATUS.PLAYING)
        session.try_pause_async = AsyncMock(return_value=False)
        pauser = media.MediaPauser(Mock(get_current_session=lambda: session), STATUS)
        await pauser.pause()
        session.status = STATUS.PAUSED
        await pauser.resume()
        session.try_play_async.assert_not_called()

    async def test_app_switch_and_manual_stop_are_respected(self):
        for change in ("app", "stop"):
            with self.subTest(change=change):
                session = Session(STATUS.PLAYING)
                manager = Mock(get_current_session=Mock(return_value=session))
                pauser = media.MediaPauser(manager, STATUS)
                await pauser.pause()
                if change == "app":
                    manager.get_current_session.return_value = NS(source_app_user_model_id="other")
                else:
                    session.status = STATUS.STOPPED
                await pauser.resume()
                session.try_play_async.assert_not_called()

    async def test_missing_session_is_noop(self):
        pauser = media.MediaPauser(Mock(get_current_session=lambda: None), STATUS)
        await pauser.pause()
        await pauser.resume()
        self.assertIsNone(pauser.paused)

    async def test_shutdown_restores_during_speech(self):
        stop = threading.Event()
        activity = media.MicActivity()
        activity.set(True)

        async def pause():
            stop.set()

        pauser = NS(pause=AsyncMock(side_effect=pause), resume=AsyncMock())
        with patch.object(media, "SPEECH_ON", 0), patch.object(media, "POLL", 0):
            await asyncio.wait_for(media._watch(activity, stop, pauser), 1)
        pauser.pause.assert_awaited_once()
        pauser.resume.assert_awaited_once()


class RuntimeTests(unittest.TestCase):
    def test_watcher_initializes_windows_runtime_and_closes(self):
        stop = threading.Event()
        activity = media.MicActivity()
        with patch.object(media, "_watch_live", new_callable=AsyncMock) as watch, patch("builtins.print") as log:
            watcher = media.start_pause_watching(activity, stop)
            watcher.join(5)
        self.assertFalse(watcher.is_alive())
        watch.assert_awaited_once_with(activity, stop)
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
