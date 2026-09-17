"""Offline regression checks. Run with: py -m unittest discover -s tests -v."""
import asyncio
import contextlib
import io
import os
import queue
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from ghost import audio_ducking, upgrade_runner

# Client construction needs a key, but these tests never open a cloud session.
with patch.dict(os.environ, {"GOOGLE_API_KEY": "offline-test-key"}):
    from ghost import live_voice


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def run_calls(self, calls, functions):
        stop = threading.Event()
        replies = []

        class Session:
            async def receive(self):
                yield NS(server_content=None, tool_call=NS(function_calls=calls))

            async def send_tool_response(self, **kwargs):
                replies.extend(kwargs["function_responses"])
                stop.set()

        session = Session()
        with patch.dict(live_voice.FUNCTIONS, functions), contextlib.redirect_stdout(io.StringIO()):
            await live_voice._receive_loop(session, NS(set=lambda *a: None), None, stop, {})
        return replies

    async def test_microphone_streams_while_tool_is_running(self):
        trace = []
        stop = threading.Event()
        mic_q = queue.Queue()

        def slow_tool():
            trace.append("tool-start")
            time.sleep(0.15)
            trace.append("tool-end")
            return "finished"

        class Session:
            async def receive(self):
                yield NS(server_content=None, tool_call=NS(function_calls=[
                    NS(name="slow_tool", args={}, id="slow")]))

            async def send_realtime_input(self, **kwargs):
                trace.append("mic-send")

            async def send_tool_response(self, **kwargs):
                stop.set()

        async def produce_audio():
            await asyncio.sleep(0.02)
            mic_q.put(b"\x00\x00")

        session = Session()
        with (
            patch.dict(live_voice.FUNCTIONS, {"slow_tool": slow_tool}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            receive = asyncio.create_task(live_voice._receive_loop(
                session, NS(set=lambda *a: None), None, stop, {}))
            mic = asyncio.create_task(live_voice._mic_loop(session, mic_q, stop))
            producer = asyncio.create_task(produce_audio())
            try:
                await asyncio.wait_for(receive, 3)
                await producer
            finally:
                stop.set()
                mic_q.put(None)
                await asyncio.wait_for(mic, 3)
        self.assertIn("mic-send", trace)
        self.assertLess(trace.index("mic-send"), trace.index("tool-end"))

    async def test_tools_keep_order_and_share_a_worker_thread(self):
        threads, order = [], []

        def record(value):
            threads.append(threading.get_ident())
            order.append(value)
            return value

        replies = await self.run_calls([
            NS(name="record", args={"value": value}, id=value) for value in ("one", "two")
        ], {"record": record})
        self.assertEqual(order, ["one", "two"])
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual([r.response["result"] for r in replies], order)

    async def test_tool_error_is_returned_and_next_call_runs(self):
        def broken():
            raise ValueError("test failure")

        replies = await self.run_calls([
            NS(name="broken", args={}, id="bad"),
            NS(name="good", args=None, id="good"),
        ], {"broken": broken, "good": lambda: "ok"})
        self.assertEqual([r.id for r in replies], ["bad", "good"])
        self.assertEqual(replies[0].response["result"], "Tool error: test failure")
        self.assertEqual(replies[1].response["result"], "ok")

    async def test_screen_capture_runs_off_event_loop(self):
        capture_threads, frames = [], []

        def capture():
            capture_threads.append(threading.get_ident())
            return b"jpeg-test"

        async def send(**kwargs):
            frames.append(kwargs["video"])

        stop = threading.Event()

        class Session:
            async def receive(self):
                yield NS(server_content=None, tool_call=NS(function_calls=[
                    NS(name="look_at_screen", args={}, id="screen")]))

            send_realtime_input = staticmethod(send)

            async def send_tool_response(self, **kwargs):
                stop.set()

        with (
            patch.object(live_voice, "capture_screen_jpeg", capture),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            await live_voice._receive_loop(Session(), NS(set=lambda *a: None), None, stop, {})
        self.assertNotEqual(capture_threads[0], threading.get_ident())
        self.assertEqual(frames[0].data, b"jpeg-test")
        self.assertEqual(frames[0].mime_type, "image/jpeg")

    async def test_cancellation_does_not_wait_for_a_running_tool(self):
        started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
        stop = threading.Event()

        def slow_tool():
            started.set()
            release.wait(3)

        class Session:
            async def receive(self):
                yield NS(server_content=None, tool_call=NS(function_calls=[
                    NS(name="slow_tool", args={}, id="slow")]))

            async def send_tool_response(self, **kwargs):
                raise AssertionError("cancelled tool must not send a response")

        with (
            patch.dict(live_voice.FUNCTIONS, {"slow_tool": slow_tool}),
            patch.object(live_voice, "_init_tool_thread"),
            patch.object(live_voice, "_close_tool_thread", side_effect=cleaned.set),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            receive = asyncio.create_task(live_voice._receive_loop(
                Session(), NS(set=lambda *a: None), None, stop, {}))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                receive.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(receive, 0.5)
                self.assertFalse(cleaned.is_set())
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(cleaned.wait, 3))

    async def test_briefing_failure_still_waits_for_volume_restore(self):
        stop = threading.Event()
        player, mic, watcher = Mock(), Mock(), Mock()

        class Connection:
            async def __aenter__(self):
                return Mock()

            async def __aexit__(self, *args):
                pass

        with (
            patch.object(live_voice, "AudioPlayer", return_value=player),
            patch.object(live_voice.sd, "RawInputStream", return_value=mic),
            patch.object(live_voice, "start_watching", return_value=watcher),
            patch.object(live_voice, "start_pause_watching", return_value=watcher),
            patch.object(live_voice, "_level_pump"),
            patch.object(live_voice.client.aio.live, "connect", return_value=Connection()),
            patch.object(live_voice, "should_brief", side_effect=OSError("test briefing failure")),
        ):
            with self.assertRaisesRegex(OSError, "test briefing failure"):
                await live_voice.run(Mock(), stop)
            await asyncio.sleep(0)
        self.assertTrue(stop.is_set())
        self.assertEqual(watcher.join.call_count, 2)
        mic.stop.assert_called_once_with()
        mic.close.assert_called_once_with()
        player.stop.assert_called_once_with()


class DuckingTests(unittest.TestCase):
    def test_restore_waits_for_an_in_progress_duck(self):
        entered, release, restored = threading.Event(), threading.Event(), threading.Event()

        class Volume:
            value = 0.6

            def GetMasterVolume(self):
                return self.value

            def SetMasterVolume(self, value, _):
                if threading.current_thread().name == "duck" and not entered.is_set():
                    entered.set()
                    release.wait(3)
                self.value = value

        volume = Volume()
        ducker = audio_ducking.Ducker()

        def restore():
            ducker.restore()
            restored.set()

        with (
            patch.object(audio_ducking, "_sessions", return_value=[(volume, "test")]),
            patch.object(audio_ducking, "FADE_MS", 0),
        ):
            duck = threading.Thread(target=ducker.duck, name="duck")
            duck.start()
            self.assertTrue(entered.wait(3))
            restoring = threading.Thread(target=restore)
            restoring.start()
            try:
                restored_early = restored.wait(0.05)
            finally:
                release.set()
                duck.join(3)
                restoring.join(3)
            ducker.restore()
        self.assertFalse(restored_early, "restore finished before the last duck write")
        self.assertAlmostEqual(volume.value, 0.6)
        self.assertEqual(ducker._saved, [])

    def test_watcher_restores_on_own_thread_before_join_returns(self):
        stop, ducked = threading.Event(), threading.Event()
        calls = []

        class Ducker:
            def duck(self):
                calls.append(("duck", threading.get_ident()))
                ducked.set()

            def restore(self):
                calls.append(("restore", threading.get_ident()))

        with patch.object(audio_ducking.atexit, "register"):
            watcher = audio_ducking.start_watching(NS(is_active=lambda: True), stop, Ducker())
            try:
                self.assertTrue(ducked.wait(3))
            finally:
                stop.set()
                if isinstance(watcher, threading.Thread):
                    watcher.join(3)
            self.assertIsInstance(watcher, threading.Thread)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(calls[-1][0], "restore")
        self.assertEqual(len({thread for _, thread in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())


class UpgradeTests(unittest.TestCase):
    def run_job(self, fail=None, changed=True, timeout=None):
        state, commands, notifications = {}, [], []

        def run(command, **kwargs):
            stage = command[1] if command[0] == "git" else "claude"
            commands.append(stage)
            if stage == timeout:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            stdout = " M ghost/skills/test.py" if stage == "status" and changed else ""
            if stage == "claude":
                self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
                stdout = "Completed test change"
            if stage == "commit":
                self.assertIn("Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>", command[-1])
            return NS(returncode=1 if stage == fail else 0, stdout=stdout,
                      stderr=f"{stage} rejected" if stage == fail else "")

        with (
            patch.object(upgrade_runner.sys, "argv", [
                "upgrade_runner.py", "test-job", "test upgrade request"]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder"}),
            patch.object(upgrade_runner.subprocess, "run", side_effect=run),
            patch.object(upgrade_runner, "write", side_effect=lambda _, **kw: state.update(kw)),
            patch.object(upgrade_runner, "notify", side_effect=notifications.append),
        ):
            code = upgrade_runner.main()
        return code, state, commands, notifications

    def test_failures_never_report_ready_or_continue_to_later_stages(self):
        stages = ["worktree", "claude", "status", "add", "commit", "show"]
        for stage in stages:
            with self.subTest(stage=stage):
                code, state, commands, notifications = self.run_job(fail=stage)
                self.assertEqual(code, 1)
                self.assertEqual(state["status"], "failed")
                self.assertIn(f"{stage} rejected", state["error"])
                self.assertIn("finished", state)
                self.assertEqual(commands, stages[:stages.index(stage) + 1])
                self.assertIn("failed", notifications[-1])

    def test_cli_failure_with_no_files_is_still_failed(self):
        code, state, commands, _ = self.run_job(fail="claude", changed=False)
        self.assertEqual((code, state["status"]), (1, "failed"))
        self.assertNotIn("status", commands)

    def test_successful_no_change_run_skips_commit(self):
        code, state, commands, _ = self.run_job(changed=False)
        self.assertEqual((code, state["status"]), (0, "no_changes"))
        self.assertNotIn("commit", commands)

    def test_ready_requires_successful_commit_and_inspection(self):
        code, state, commands, _ = self.run_job()
        self.assertEqual((code, state["status"]), (0, "ready"))
        self.assertEqual(commands[-2:], ["commit", "show"])

    def test_timeouts_are_recorded_as_failed(self):
        for stage in ("worktree", "claude", "commit"):
            with self.subTest(stage=stage):
                code, state, _, _ = self.run_job(timeout=stage)
                self.assertEqual((code, state["status"]), (1, "failed"))
                self.assertIn("timed out", state["error"])


if __name__ == "__main__":
    unittest.main()
