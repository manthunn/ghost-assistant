"""Wake listener: say "hey ghost" or clap twice, and Ghost comes online.

Runs local faster-whisper on short rolling audio windows. That's the point of
doing it locally rather than through the Live API: idle listening costs nothing,
whereas holding a Live session open bills per audio-minute. Ghost is only
launched (and only starts costing money) once the phrase is actually heard.

Double-clap runs on the same audio stream but never reaches whisper - it's pure
signal analysis on the raw samples, so it costs nothing and responds instantly.

    py wake.py

Complements hotkey.py - press F12, say the phrase, or clap twice.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pathlib
import subprocess
import time
import numpy as np
import sounddevice as sd

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

RATE = 16000
WINDOW_SECS = 3.0        # rolling buffer length fed to whisper
HOP_SECS = 1.0           # how often we transcribe
SPEECH_THRESH = 0.012    # skip transcription entirely on near-silence (saves CPU)
COOLDOWN_SECS = 25       # after a launch, ignore audio (Ghost's own voice included)

# Whisper mangles "ghost" in predictable ways; accept the near-misses rather than
# making the user enunciate. Kept deliberately tight to limit false positives.
# NB: bare "ghost" or "a ghost" are deliberately NOT here - they'd fire on
# ordinary conversation ("a ghost town", "the ghost of..."). The wake phrase has
# to include the greeting word.
PHRASES = (
    "hey ghost", "hay ghost", "hey goast", "hey gost", "hey ghosts",
    "hey go st", "okay ghost", "hey guest", "ok ghost",
)

# --- double-clap detection -------------------------------------------------
# A clap is a very short broadband burst with an almost instant attack. Speech
# ramps up comparatively slowly and music sits at a sustained level, so the
# discriminator is "sudden, loud relative to the moment before, and brief" -
# not loudness alone.
CLAP_ENABLED = True
CLAP_FRAME_SECS = 0.008      # 8 ms analysis frames
CLAP_ABS_MIN = 0.18          # absolute peak floor; claps are genuinely loud
CLAP_RATIO = 6.0             # ...and this much above the running background
CLAP_DECAY_SECS = 0.09       # must fall back to background within this
CLAP_REFRACTORY = 0.10       # ignore this long after an onset (echo, decay)
CLAP_MIN_GAP = 0.12          # two claps closer than this are one clap echoing
CLAP_MAX_GAP = 0.70          # ...further apart than this isn't a double clap
CLAP_BG_DECAY = 0.995        # background tracker: slow to rise, quick to forget


class ClapDetector:
    """Reports when two claps land within CLAP_MIN_GAP..CLAP_MAX_GAP.

    Deliberately requires the onset to rise sharply out of a quiet background
    rather than just exceeding a fixed level: with music playing, the running
    background rises, so the bar for "sudden" rises with it. That is what stops
    a track with claps in it from launching Ghost - though loud percussive
    music over speakers can still fool it, hence CLAP_ENABLED.
    """

    def __init__(self, rate):
        self.frame = max(1, int(CLAP_FRAME_SECS * rate))
        self.rate = rate
        self.bg = 0.02
        self.pending = None      # time of the first clap of a possible pair
        self.blocked_until = 0.0
        self.armed_at = None     # onset being checked for a fast decay

    def feed(self, samples, block_start):
        """samples: float32 mono. block_start: absolute time of sample 0.

        Returns the time of the second clap when a pair completes, else None.
        """
        hit = None
        n = len(samples)
        for i in range(0, n - self.frame, self.frame):
            t = block_start + i / self.rate
            peak = float(np.abs(samples[i:i + self.frame]).max())

            # Confirm a previous onset actually decayed like a clap rather than
            # being the start of a shout or a sustained noise.
            if self.armed_at is not None and t - self.armed_at >= CLAP_DECAY_SECS:
                decayed = peak < max(CLAP_ABS_MIN * 0.5, self.bg * 3.0)
                onset = self.armed_at
                self.armed_at = None
                if decayed:
                    hit = self._register(onset) or hit

            if t >= self.blocked_until and self.armed_at is None:
                if peak >= CLAP_ABS_MIN and peak >= self.bg * CLAP_RATIO:
                    self.armed_at = t
                    self.blocked_until = t + CLAP_REFRACTORY

            # Track background from quiet frames only, so a clap doesn't raise
            # the bar against its own partner.
            if peak < CLAP_ABS_MIN:
                self.bg = max(0.005, self.bg * CLAP_BG_DECAY + peak * (1 - CLAP_BG_DECAY))
        return hit

    def _register(self, t):
        if self.pending is not None and CLAP_MIN_GAP <= t - self.pending <= CLAP_MAX_GAP:
            self.pending = None
            return t
        self.pending = t          # first clap, or too late to pair - restart
        return None

    def reset(self):
        self.pending = None
        self.armed_at = None


_proc = None

def _model():
    print("Loading wake-word ears (local whisper)...")
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("small.en", device="cuda", compute_type="float16")
        print("  using GPU")
    except Exception as e:
        print(f"  GPU unavailable ({e}); using CPU")
        m = WhisperModel("small.en", device="cpu", compute_type="int8")
    return m

def _heard_wake(text):
    t = " ".join(text.lower().replace(",", " ").replace(".", " ").split())
    return any(p.replace(",", "").replace(".", "").strip() in t for p in PHRASES)

def _launch():
    global _proc
    if _proc is not None and _proc.poll() is None:
        print("  Ghost is already running.")
        return False
    print("  >>> wake word heard - starting Ghost")
    _proc = subprocess.Popen([sys.executable, str(MAIN)], cwd=str(ROOT))
    return True

def main():
    if not MAIN.exists():
        print(f"Can't find {MAIN} - run this from the ghost-assistant folder.")
        return
    model = _model()
    buf = np.zeros(0, dtype=np.float32)
    window = int(WINDOW_SECS * RATE)
    hop = int(HOP_SECS * RATE)
    muted_until = 0.0
    claps = ClapDetector(RATE)

    trigger = '"hey ghost"' + (" or a double clap" if CLAP_ENABLED else "")
    print(f"Listening for {trigger} (Ctrl+C to stop)...")
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                        blocksize=hop) as stream:
        while True:
            block, _ = stream.read(hop)
            samples = block.flatten()
            block_start = time.time() - len(samples) / RATE
            buf = np.concatenate([buf, samples])[-window:]

            if time.time() < muted_until:
                claps.reset()   # don't pair a clap from before the mute with one after
                continue

            # Checked before transcription: it's cheap, and a clap carries no
            # words for whisper to find anyway.
            if CLAP_ENABLED and claps.feed(samples, block_start) is not None:
                print("  >>> double clap heard")
                if _launch():
                    muted_until = time.time() + COOLDOWN_SECS
                claps.reset()
                buf = np.zeros(0, dtype=np.float32)
                continue
            if len(buf) < window:
                continue
            if np.abs(buf).max() < SPEECH_THRESH:
                continue  # silence, don't bother transcribing

            segs, _info = model.transcribe(buf, language="en", vad_filter=True)
            text = " ".join(s.text for s in segs).strip()
            if not text:
                continue
            print(f"  heard: {text!r}")
            if _heard_wake(text):
                if _launch():
                    muted_until = time.time() + COOLDOWN_SECS
                buf = np.zeros(0, dtype=np.float32)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWake-word listener stopped.")
