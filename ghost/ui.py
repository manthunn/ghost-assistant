import queue
import tkinter as tk

BG = "#14141a"
BORDER = "#2a2a35"
TEXT = "#f0f0f5"
DETAIL = "#8a8a99"
CLOSE_HOVER = "#e5484d"

STATE_COLORS = {
    "Listening": "#3ecf6a",
    "Thinking": "#f5b83d",
    "Working": "#4a9eff",
    "Speaking": "#b78cff",
    "Offline": "#5a5a66",
    "Booting": "#5a5a66",
}

def _rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)

def _status_word(state_text):
    for word in STATE_COLORS:
        if word.lower() in state_text.lower():
            return word
    return "Booting"

class GhostUI:
    WIDTH, HEIGHT = 260, 64

    def __init__(self, on_close=None):
        self.on_close = on_close
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Ghost")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+40+40")

        self.canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.HEIGHT,
                                 bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        _rounded_rect(self.canvas, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 16,
                      fill=BG, outline=BORDER, width=1)

        self.dot = self.canvas.create_oval(16, 22, 36, 42, fill=STATE_COLORS["Booting"],
                                            outline="")
        self.state_text = self.canvas.create_text(
            46, 22, anchor="nw", text="Booting...", fill=TEXT,
            font=("Segoe UI", 12, "bold"))
        self.detail_text = self.canvas.create_text(
            46, 40, anchor="nw", text="", fill=DETAIL, font=("Segoe UI", 9))

        self.close_btn = self.canvas.create_text(
            self.WIDTH - 16, 14, text="✕", fill=DETAIL, font=("Segoe UI", 10))
        self.canvas.tag_bind(self.close_btn, "<Enter>",
                              lambda e: self.canvas.itemconfig(self.close_btn, fill=CLOSE_HOVER))
        self.canvas.tag_bind(self.close_btn, "<Leave>",
                              lambda e: self.canvas.itemconfig(self.close_btn, fill=DETAIL))
        self.canvas.tag_bind(self.close_btn, "<Button-1>", self._on_close_click)

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self._drag = (0, 0)

        self.root.after(100, self._poll)

    def _on_close_click(self, event):
        if self.on_close:
            self.on_close()
        self.root.destroy()

    def _drag_start(self, event):
        self._drag = (event.x, event.y)

    def _drag_move(self, event):
        x = self.root.winfo_x() + event.x - self._drag[0]
        y = self.root.winfo_y() + event.y - self._drag[1]
        self.root.geometry(f"+{x}+{y}")

    def set(self, state, detail=""):
        self.q.put((state, detail[:42]))

    def _poll(self):
        try:
            while True:
                s, d = self.q.get_nowait()
                word = _status_word(s)
                self.canvas.itemconfig(self.state_text, text=word)
                self.canvas.itemconfig(self.detail_text, text=d)
                self.canvas.itemconfig(self.dot, fill=STATE_COLORS[word])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def run(self):
        self.root.mainloop()
