"""Pause whatever's playing when the user starts talking, resume when they stop.

Companion to audio_ducking.py rather than a replacement: that module dims OTHER
apps' volume while GHOST is talking. This one pauses them when the USER is -
someone dictating or on a call usually wants the music to actually stop, not
just get quieter, and the two can be active at the same time.

Reuses the mic stream live_voice.py already has open rather than opening a
second one: two independent input streams fighting over the same device is
extra failure surface for no benefit when the samples are already flowing.
mic_callback feeds a loudness flag into MicActivity (has to stay cheap - it
runs on the audio callback thread); a plain poll thread applies hysteresis and
sends the action, decoupled from anything realtime-sensitive.

Windows only exposes play/pause as a toggle, not explicit play/pause states -
the same limitation media.py's media_control already lives with. So this only
resumes what it paused: silence right after the user manually paused their own
music won't make it play again, because nothing here re-pauses blind - it only
reverts its own action.
"""
import threading
import time
import keyboard

SPEECH_RMS = 300     # int16 RMS above this counts as speech; tune to your mic
SPEECH_ON = 0.25     # seconds of continuous speech before pausing
SPEECH_OFF = 1.2     # seconds of silence before resuming
POLL = 0.05


class MicActivity:
    """Thread-safe latch: mic_callback writes it, the watcher thread reads it."""

    def __init__(self):
        self._loud = False
        self._lock = threading.Lock()

    def set(self, loud):
        with self._lock:
            self._loud = loud

    def is_loud(self):
        with self._lock:
            return self._loud


def start_pause_watching(activity, stop_event):
    """Poll `activity` and toggle media playback on sustained speech/silence."""

    def loop():
        speaking = False
        paused_by_us = False
        since = time.monotonic()
        while not stop_event.is_set():
            now = time.monotonic()
            loud = activity.is_loud()
            if loud != speaking:
                speaking, since = loud, now
            elif speaking and not paused_by_us and now - since >= SPEECH_ON:
                keyboard.send("play/pause media")
                paused_by_us = True
            elif not speaking and paused_by_us and now - since >= SPEECH_OFF:
                keyboard.send("play/pause media")
                paused_by_us = False
            time.sleep(POLL)
        if paused_by_us:
            # Don't leave the user's music paused because the session ended.
            try:
                keyboard.send("play/pause media")
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
