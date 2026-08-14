"""Dim other apps' audio while Ghost is speaking, then put it back.

Per-application volume through the Windows Core Audio API, so Spotify and a
YouTube tab drop while Ghost talks and Ghost's own voice stays at full level.
Nothing pauses and nothing loses its place - it's a volume change, not a
transport command, which is why this works on "whatever is playing" rather than
needing per-app integrations.

Two things this has to get right:

Ghost's own session is excluded by PID. Ducking every session including our own
would just make Ghost quieter along with the music, which is the opposite of
the point.

The original volume is captured at duck time, not assumed to be 1.0. If Spotify
was at 40%, restoring to 100% would be louder than the user left it, and they'd
have to fix it by hand every time Ghost spoke.

COM must be initialised on whichever thread touches these interfaces - the
watcher runs on its own thread, so it initialises its own apartment. This is
the same class of trap as the pywinauto import deadlock noted in
ui_automation.
"""
import atexit
import os
import threading
import time

DUCK_LEVEL = 0.20      # fraction of the app's own volume while Ghost speaks
FADE_MS = 180          # short fade; an instant cut is audibly jarring
FADE_STEPS = 8
RELEASE_DELAY = 0.35   # silence this long before restoring, so gaps between
                       # audio chunks don't flicker the volume up and down
POLL = 0.05


def _sessions(exclude_pids):
    """(ISimpleAudioVolume, name) for every other app currently making sound."""
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    out = []
    try:
        all_sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return out
    for s in all_sessions:
        try:
            if not s.Process or s.Process.pid in exclude_pids:
                continue
            vol = s._ctl.QueryInterface(ISimpleAudioVolume)
            out.append((vol, s.Process.name()))
        except Exception:
            continue      # session died mid-enumeration; skip it
    return out


class Ducker:
    """Idempotent duck/restore of every audio session except our own."""

    def __init__(self, level=DUCK_LEVEL, exclude_pids=None):
        self.level = level
        self.exclude = set(exclude_pids or ()) | {os.getpid()}
        self._saved = []          # [(ISimpleAudioVolume, original_level)]
        self._ducked = False
        self._lock = threading.Lock()

    def duck(self):
        with self._lock:
            if self._ducked:
                return
            saved = []
            for vol, _name in _sessions(self.exclude):
                try:
                    cur = vol.GetMasterVolume()
                    if cur <= 0.01:
                        continue          # already silent; leave it alone
                    saved.append((vol, cur))
                except Exception:
                    continue
            if not saved:
                self._ducked = True       # nothing playing; still mark state
                self._saved = []
                return
            self._saved = saved
            self._ducked = True
        self._fade([(v, cur, cur * self.level) for v, cur in saved])

    def restore(self):
        with self._lock:
            if not self._ducked:
                return
            saved, self._saved, self._ducked = self._saved, [], False
        self._fade([(v, cur * self.level, cur) for v, cur in saved])

    def _fade(self, moves):
        step = FADE_MS / 1000.0 / max(1, FADE_STEPS)
        for i in range(1, FADE_STEPS + 1):
            f = i / FADE_STEPS
            for vol, start, end in moves:
                try:
                    vol.SetMasterVolume(start + (end - start) * f, None)
                except Exception:
                    continue      # app closed mid-fade
            time.sleep(step)


def start_watching(player, stop_event, ducker=None):
    """Duck while `player` has audio queued; restore shortly after it drains.

    Polls rather than hooking the audio callback: that callback runs on the
    sound thread and has to stay cheap, and COM calls there would risk glitching
    playback.

    Returns the Ducker so the caller can force a restore on shutdown.
    """
    ducker = ducker or Ducker()

    def loop():
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        last_active = 0.0
        try:
            while not stop_event.is_set():
                try:
                    active = player.is_active()
                except Exception:
                    active = False
                now = time.monotonic()
                if active:
                    last_active = now
                    ducker.duck()
                elif now - last_active > RELEASE_DELAY:
                    ducker.restore()
                time.sleep(POLL)
        finally:
            # Never leave the user's music quiet because Ghost went away.
            try:
                ducker.restore()
            except Exception:
                pass
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
    # Belt and braces: a crash that skips the finally still restores on exit.
    atexit.register(lambda: _safe_restore(ducker))
    return ducker


def _safe_restore(ducker):
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        ducker.restore()
    except Exception:
        pass
