import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import threading
from ghost import live_voice
from ghost.ui3d import GhostUI
from ghost.skills import load_all

def assistant_loop(ui, stop_event):
    ui.set("🟣 Loading skills...")
    print("👻 Ghost booting...")
    load_all()
    ui.set("🟢 Listening", "say 'goodbye ghost' to exit")
    try:
        asyncio.run(live_voice.run(ui, stop_event))
    except Exception as e:
        print(f"Ghost session ended with an error: {e}")
    ui.set("⚫ Offline")
    try:
        ui.window.destroy()
    except Exception:
        pass

if __name__ == "__main__":
    stop_event = threading.Event()
    ui = GhostUI(on_close=stop_event.set)
    threading.Thread(target=assistant_loop, args=(ui, stop_event), daemon=True).start()
    ui.run()