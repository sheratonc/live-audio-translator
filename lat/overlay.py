"""Always-on-top subtitle window.

Drag to move, mouse wheel = text size, Ctrl+wheel = width, right-click = hide.
'Click-through' lets clicks pass to the game/video underneath (unlock from the main window).
"""

import ctypes
import tkinter as tk

BG = "#101014"


class Overlay:
    def __init__(self, root, cfg, on_hide=None):
        self.cfg = cfg
        self.on_hide = on_hide
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", cfg.get("overlay_alpha", 0.85))
        self.win.configure(bg=BG)

        self.width = 900
        geo = cfg.get("overlay_geometry")
        if geo:
            self.win.geometry(geo)
            try:
                self.width = int(geo.split("x")[0])
            except ValueError:
                pass
        else:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            self.win.geometry(f"{self.width}x10+{(sw - self.width) // 2}+{int(sh * 0.78)}")

        self.main = tk.Label(self.win, text="", bg=BG, fg="#ffffff", justify="center")
        self.sub = tk.Label(self.win, text="", bg=BG, fg="#9aa0aa", justify="center")
        self._clear_job = None
        self.click_through = False
        self.main.pack(fill="x", padx=16, pady=(10, 2))
        self.sub.pack(fill="x", padx=16, pady=(0, 10))
        self._apply_font()
        self.set_text("Live translation will appear here", "drag to move · wheel = size · ctrl+wheel = width · right-click = hide")

        for w in (self.win, self.main, self.sub):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", lambda e: self._save_geo())
            w.bind("<MouseWheel>", self._wheel)
            w.bind("<Button-3>", lambda e: self.hide(user=True))

    # -- display -------------------------------------------------------------
    def set_text(self, main, sub="", partial=False):
        self.main.configure(text=main, fg="#c9cdd4" if partial else "#ffffff")
        show_sub = bool(sub) and self.cfg.get("overlay_show_source", True)
        self.sub.configure(text=sub if show_sub else "")
        if show_sub:
            self.sub.pack(fill="x", padx=16, pady=(0, 10))
        else:
            self.sub.pack_forget()
        self._fit()
        # nearly invisible thin bar while idle, so it can still be found and dragged
        self.win.attributes("-alpha", self.cfg.get("overlay_alpha", 0.85) if main else 0.25)
        if self._clear_job:
            self.win.after_cancel(self._clear_job)
        self._clear_job = self.win.after(9000, lambda: self.set_text("", ""))  # fade out stale lines

    def _apply_font(self):
        size = int(self.cfg.get("overlay_font", 26))
        self.main.configure(font=("Segoe UI Semibold", size), wraplength=self.width - 32)
        self.sub.configure(font=("Segoe UI", max(9, int(size * 0.55))), wraplength=self.width - 32)
        self._fit()

    def _fit(self):
        self.win.update_idletasks()
        h = self.main.winfo_reqheight() + (self.sub.winfo_reqheight() + 10 if self.sub.winfo_ismapped() else 0) + 14
        x, y = self.win.winfo_x(), self.win.winfo_y()
        self.win.geometry(f"{self.width}x{max(h, 20)}+{x}+{y}")

    # -- interaction -----------------------------------------------------------
    def _start_drag(self, e):
        self._dx, self._dy = e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y()

    def _drag(self, e):
        self.win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _wheel(self, e):
        step = 1 if e.delta > 0 else -1
        if e.state & 0x4:  # Ctrl
            self.width = max(300, min(self.win.winfo_screenwidth(), self.width + step * 60))
        else:
            self.cfg["overlay_font"] = max(10, min(72, int(self.cfg.get("overlay_font", 26)) + step * 2))
        self._apply_font()
        self._save_geo()

    def _save_geo(self):
        self.cfg["overlay_geometry"] = f"{self.width}x{self.win.winfo_height()}+{self.win.winfo_x()}+{self.win.winfo_y()}"

    def set_alpha(self, a):
        self.cfg["overlay_alpha"] = a
        if self.main.cget("text"):
            self.win.attributes("-alpha", a)

    def set_click_through(self, on):
        """Let mouse clicks pass through the overlay (Windows only)."""
        self.click_through = on
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            flags = 0x20 | 0x80000  # WS_EX_TRANSPARENT | WS_EX_LAYERED
            style = style | flags if on else style & ~0x20
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def show(self):
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.set_click_through(self.click_through)

    def hide(self, user=False):
        self._save_geo()
        self.win.withdraw()
        if user and self.on_hide:
            self.on_hide()
