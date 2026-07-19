import queue
import tkinter as tk

class GhostUI:
    def __init__(self):
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Ghost")
        self.root.geometry("280x86+40+40")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#101014")
        self.state = tk.Label(self.root, text="🟣 Booting...",
                              fg="#b78cff", bg="#101014",
                              font=("Segoe UI", 14, "bold"))
        self.state.pack(expand=True)
        self.detail = tk.Label(self.root, text="", fg="#777",
                               bg="#101014", font=("Segoe UI", 9))
        self.detail.pack(pady=(0, 8))
        self.root.after(100, self._poll)

    def set(self, state, detail=""):
        self.q.put((state, detail[:42]))

    def _poll(self):
        try:
            while True:
                s, d = self.q.get_nowait()
                self.state.config(text=s)
                self.detail.config(text=d)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def run(self):
        self.root.mainloop()