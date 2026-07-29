import pyttsx3
import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel

SAMPLERATE = 16000
SILENCE_THRESH = 0.015   # raise to 0.025 if noisy room
SILENCE_SECS = 1.2       # pause length that ends your sentence
MAX_SECS = 30
WAIT_SECS = 600          # hands-free: waits up to 10 min for speech

_whisper = None

# Domain vocabulary that's easy to mis-hear otherwise (app names, places, etc.)
VOCAB_HINT = ("Ghost, Vivaldi, Notepad, Spotify, VS Code, Deakin Hall, Clayton, "
              "Monash, AFL, NRL")

def load_ears():
    global _whisper
    if _whisper is None:
        print("Loading ears...")
        try:
            _whisper = WhisperModel("small.en", device="cuda", compute_type="float16")
        except Exception as e:
            print(f"  GPU unavailable for Whisper ({e}), falling back to CPU.")
            _whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    return _whisper

def speak(text):
    text = text.strip()
    if not text:
        return
    eng = pyttsx3.init()
    eng.setProperty("rate", 185)
    eng.say(text)
    eng.runAndWait()
    eng.stop()

def _record_until_silence():
    block = int(0.1 * SAMPLERATE)
    chunks, silent, waited = [], 0, 0
    started = False
    with sd.InputStream(samplerate=SAMPLERATE, channels=1,
                        dtype="float32", blocksize=block) as stream:
        while True:
            data, _ = stream.read(block)
            level = np.abs(data).max()
            if not started:
                waited += 1
                if level > SILENCE_THRESH:
                    started = True
                    chunks.append(data.copy())
                elif waited * 0.1 >= WAIT_SECS:
                    return None
            else:
                chunks.append(data.copy())
                silent = silent + 1 if level < SILENCE_THRESH else 0
                if silent * 0.1 >= SILENCE_SECS or len(chunks) * 0.1 >= MAX_SECS:
                    break
    return np.concatenate(chunks).flatten()

def listen():
    audio = _record_until_silence()
    if audio is None:
        return ""
    segments, _ = load_ears().transcribe(
        audio, language="en", vad_filter=True, initial_prompt=VOCAB_HINT)
    return " ".join(s.text for s in segments).strip()