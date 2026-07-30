"""Wake-word listener: say "hey ghost" and Ghost comes online.

Runs local faster-whisper on short rolling audio windows. That's the point of
doing it locally rather than through the Live API: idle listening costs nothing,
whereas holding a Live session open bills per audio-minute. Ghost is only
launched (and only starts costing money) once the phrase is actually heard.

    py wake.py

Complements hotkey.py - press F12 or say the phrase, whichever suits.
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

    print(f'Listening for "hey ghost" (Ctrl+C to stop)...')
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                        blocksize=hop) as stream:
        while True:
            block, _ = stream.read(hop)
            buf = np.concatenate([buf, block.flatten()])[-window:]

            if time.time() < muted_until:
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
