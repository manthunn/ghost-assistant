"""Measure real claps on this machine's mic and report what the detector does.

The thresholds in wake.py were first set against synthetic audio, which is not
evidence about a particular microphone in a particular room. This records you
actually clapping, prints the levels, and runs the real ClapDetector over the
recording so the answer is "it would/wouldn't have fired", not a guess.

    py clap_calibrate.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import time
import numpy as np
import sounddevice as sd

import wake
from wake import ClapDetector, RATE

RECORD_SECS = 14
FRAME = 128


def main():
    dev = sd.query_devices(kind="input")
    print(f"Input device: {dev['name']}")
    print()
    print(f"Recording {RECORD_SECS} seconds.")
    print("  1. Stay quiet for 3 seconds.")
    print("  2. CLAP TWICE, normally, from where you'd actually sit.")
    print("  3. Pause a couple of seconds, then CLAP TWICE again.")
    print("  4. Say 'hey ghost' out loud, so we can compare speech to claps.")
    print()
    for i in (3, 2, 1):
        print(f"  starting in {i}...")
        time.sleep(1)
    print("  GO")

    audio = sd.rec(int(RECORD_SECS * RATE), samplerate=RATE,
                   channels=1, dtype="float32")
    sd.wait()
    audio = audio.flatten()
    print("  done.\n")

    peaks = np.array([np.abs(audio[i:i + FRAME]).max()
                      for i in range(0, len(audio) - FRAME, FRAME)])
    bg = float(np.median(peaks))
    loud = np.sort(peaks)[-12:][::-1]

    print("=== levels ===")
    print(f"  background (median frame peak): {bg:.5f}")
    print(f"  95th percentile              : {np.percentile(peaks, 95):.5f}")
    print(f"  loudest 12 frames            : "
          + ", ".join(f"{p:.3f}" for p in loud))
    print(f"  absolute max                 : {peaks.max():.4f}")

    print("\n=== current settings ===")
    print(f"  CLAP_ABS_MIN = {wake.CLAP_ABS_MIN}   CLAP_RATIO = {wake.CLAP_RATIO}")
    over = int((peaks >= wake.CLAP_ABS_MIN).sum())
    print(f"  frames reaching CLAP_ABS_MIN: {over}"
          + ("   <-- nothing reached it, so nothing could ever fire" if not over else ""))

    det = ClapDetector(RATE)
    hits, t = 0, 0.0
    for i in range(0, len(audio) - RATE, RATE):
        if det.feed(audio[i:i + RATE], t) is not None:
            hits += 1
        t += 1.0
    print(f"  double-claps the real detector found: {hits}")

    # Suggest a floor that sits well above this room but below the claps seen.
    if peaks.max() > bg * 20:
        suggest = max(0.02, round(float(loud[3]) * 0.45, 3))
        print("\n=== suggested ===")
        print(f"  CLAP_ABS_MIN = {suggest}")
        print(f"  (comfortably above this room's {bg:.5f} background, "
              f"below the claps at ~{loud[0]:.2f})")
        print("  Tell Claude these numbers and it'll set them.")
    else:
        print("\n  No clear transients stood out from the background - the mic may")
        print("  not have picked the claps up at all. Try clapping closer to it.")


if __name__ == "__main__":
    main()
