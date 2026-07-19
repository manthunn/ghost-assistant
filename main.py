import threading
from ghost import voice, brain
from ghost.ui import GhostUI
from ghost.skills import load_all

def assistant_loop(ui):
    ui.set("🟣 Loading skills...")
    print("👻 Ghost booting...")
    load_all()
    voice.load_ears()
    ui.set("🟢 Listening", "say 'goodbye ghost' to exit")
    voice.speak("Ghost online.")
    while True:
        ui.set("🟢 Listening")
        heard = voice.listen()
        if not heard:
            continue
        print(f"\nYou: {heard}")
        if "goodbye ghost" in heard.lower():
            ui.set("⚫ Offline")
            voice.speak("Goodbye.")
            break
        ui.set("🟡 Thinking", heard)
        reply = brain.think(heard,
                            status=lambda d: ui.set("🔵 Working", d))
        print(f"Ghost: {reply}\n")
        ui.set("🟣 Speaking")
        voice.speak(reply)
    ui.root.after(300, ui.root.destroy)

if __name__ == "__main__":
    ui = GhostUI()
    threading.Thread(target=assistant_loop, args=(ui,), daemon=True).start()
    ui.run()