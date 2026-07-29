import pathlib
import threading
import webview

STATE_WORDS = ("Listening", "Thinking", "Working", "Speaking", "Offline", "Booting")
HTML_PATH = pathlib.Path(__file__).resolve().parent / "webui" / "index.html"

def _extract_word(state_text):
    for w in STATE_WORDS:
        if w.lower() in state_text.lower():
            return w
    return "Booting"

class GhostUI:
    """Fullscreen 3D universe status view, drop-in replacement for the old tkinter overlay."""

    def __init__(self, on_close=None):
        self.on_close = on_close
        self._ready = False
        self._lock = threading.Lock()
        self._pending = ("Booting", "")
        self.window = webview.create_window(
            "Ghost", str(HTML_PATH), fullscreen=True, frameless=True, easy_drag=False)
        self.window.events.loaded += self._on_loaded
        self.window.events.closed += self._on_closed

    def _on_loaded(self):
        with self._lock:
            self._ready = True
            state, detail = self._pending
        self._push(state, detail)

    def _on_closed(self):
        if self.on_close:
            self.on_close()

    def _push(self, state, detail):
        word = _extract_word(state)
        safe_detail = (detail or "")[:80].replace("\\", "\\\\").replace("'", "\\'")
        try:
            self.window.evaluate_js(f"window.updateGhostState('{word}', '{safe_detail}')")
        except Exception:
            pass  # window not up yet or already closed

    def set(self, state, detail=""):
        with self._lock:
            ready = self._ready
            self._pending = (state, detail)
        if ready:
            self._push(state, detail)

    def run(self):
        webview.start()
