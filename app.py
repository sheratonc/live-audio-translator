"""Live Audio Translator - translates what your PC is playing, live.

Run:  .venv\\Scripts\\pythonw app.py   (or double-click "Live Audio Translator.bat")
"""

import os
import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lat import audio, config, languages, translators  # noqa: E402
from lat.engine import Pipeline  # noqa: E402
from lat.overlay import Overlay  # noqa: E402

MODELS = [
    ("tiny", "fastest, least accurate (~75 MB)"),
    ("base", "fast (~145 MB)"),
    ("small", "good balance - recommended (~480 MB)"),
    ("medium", "more accurate, slower (~1.5 GB)"),
    ("large-v3-turbo", "best accuracy, heavy on CPU (~1.6 GB)"),
    ("large-v3", "most accurate, slowest (~3 GB)"),
]


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = config.load()
        self.pipeline = None
        self.events = queue.Queue()
        self.lines = []  # (time, source, translation)
        self.partial_mark = None

        root.title("Live Audio Translator")
        root.minsize(640, 460)
        if self.cfg.get("window_geometry"):
            root.geometry(self.cfg["window_geometry"])
        else:
            root.geometry("780x560")
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Big.TButton", font=("Segoe UI Semibold", 11), padding=6)

        self._build()
        self.overlay = Overlay(root, self.cfg, on_hide=lambda: self.overlay_var.set(False))
        if self.cfg.get("overlay"):
            self.overlay.show()
        self.refresh_devices()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Audio source").grid(row=0, column=0, sticky="w", **pad)
        self.device_var = tk.StringVar(value=self.cfg["device"])
        self.device_cb = ttk.Combobox(top, textvariable=self.device_var, state="readonly")
        self.device_cb.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Button(top, text="Refresh", command=self.refresh_devices).grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(top, text="From").grid(row=1, column=0, sticky="w", **pad)
        self.src_var = tk.StringVar(value=self.cfg["source"])
        ttk.Combobox(top, textvariable=self.src_var, values=languages.SOURCE_CHOICES,
                     state="readonly", height=20).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(top, text="To").grid(row=1, column=2, sticky="e", **pad)
        self.tgt_var = tk.StringVar(value=self.cfg["target"])
        ttk.Combobox(top, textvariable=self.tgt_var, values=languages.TARGET_CHOICES,
                     state="readonly", height=20).grid(row=1, column=3, sticky="ew", **pad)

        ttk.Label(top, text="Translator").grid(row=2, column=0, sticky="w", **pad)
        self.prov_var = tk.StringVar(value=self.cfg["provider"])
        prov_cb = ttk.Combobox(top, textvariable=self.prov_var, values=[p.name for p in translators.PROVIDERS],
                               state="readonly")
        prov_cb.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        prov_cb.bind("<<ComboboxSelected>>", lambda e: self._update_prov_hint())
        ttk.Button(top, text="API settings...", command=self.api_settings).grid(row=2, column=3, sticky="w", **pad)
        self.prov_hint = ttk.Label(top, text="", foreground="#666")
        self.prov_hint.grid(row=3, column=1, columnspan=3, sticky="w", padx=6)

        ttk.Label(top, text="Speech model").grid(row=4, column=0, sticky="w", **pad)
        self.model_var = tk.StringVar(value=self._model_label(self.cfg["model"]))
        ttk.Combobox(top, textvariable=self.model_var, values=[f"{m} - {d}" for m, d in MODELS],
                     state="readonly").grid(row=4, column=1, columnspan=2, sticky="ew", **pad)

        opts = ttk.Frame(top)
        opts.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.partial_var = tk.BooleanVar(value=self.cfg["show_partials"])
        ttk.Checkbutton(opts, text="Live partial text (lower latency, more API usage)",
                        variable=self.partial_var).pack(side="left", padx=6)
        self.overlay_var = tk.BooleanVar(value=self.cfg["overlay"])
        ttk.Checkbutton(opts, text="Subtitle overlay", variable=self.overlay_var,
                        command=self._toggle_overlay).pack(side="left", padx=6)
        self.showsrc_var = tk.BooleanVar(value=self.cfg["overlay_show_source"])
        ttk.Checkbutton(opts, text="Show original", variable=self.showsrc_var,
                        command=lambda: self.cfg.update(overlay_show_source=self.showsrc_var.get())).pack(side="left", padx=6)
        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Click-through", variable=self.lock_var,
                        command=lambda: self.overlay.set_click_through(self.lock_var.get())).pack(side="left", padx=6)
        ttk.Label(opts, text="Opacity").pack(side="left", padx=(12, 2))
        self.alpha = ttk.Scale(opts, from_=0.3, to=1.0, value=self.cfg["overlay_alpha"], length=90,
                               command=lambda v: self.overlay.set_alpha(round(float(v), 2)))
        self.alpha.pack(side="left")

        bar = ttk.Frame(self.root, padding=(10, 0))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="Start translating", style="Big.TButton", command=self.toggle)
        self.start_btn.pack(side="left")
        self.meter = ttk.Progressbar(bar, length=120, maximum=100)
        self.meter.pack(side="left", padx=10)
        self.status = ttk.Label(bar, text="Ready.", foreground="#444")
        self.status.pack(side="left", fill="x", expand=True)

        mid = ttk.Frame(self.root, padding=10)
        mid.pack(fill="both", expand=True)
        self.text = tk.Text(mid, wrap="word", font=("Segoe UI", 11), relief="flat", bg="#fbfbfc",
                            padx=8, pady=6, state="disabled")
        sb = ttk.Scrollbar(mid, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("src", foreground="#777", font=("Segoe UI", 9))
        self.text.tag_configure("tr", foreground="#111", font=("Segoe UI", 12))
        self.text.tag_configure("partial", foreground="#999", font=("Segoe UI", 12, "italic"))
        self.text.tag_configure("err", foreground="#b00020", font=("Segoe UI", 9))
        self.text.mark_set("pstart", "end")
        self.text.mark_gravity("pstart", "left")

        foot = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        foot.pack(fill="x")
        ttk.Button(foot, text="Clear", command=self.clear).pack(side="left")
        ttk.Button(foot, text="Save transcript...", command=self.save_transcript).pack(side="left", padx=6)
        ttk.Label(foot, text="Tip: pick the exact output (e.g. a GoXLR channel) the audio plays on.",
                  foreground="#888").pack(side="right")
        self._update_prov_hint()

    def _model_label(self, m):
        return next((f"{n} - {d}" for n, d in MODELS if n == m), f"{m}")

    def _update_prov_hint(self):
        p = translators.BY_NAME.get(self.prov_var.get(), translators.GoogleFree)
        self.prov_hint.configure(text=p.description)

    def refresh_devices(self):
        try:
            devs = audio.list_devices()
        except Exception as e:
            devs = [audio.DEFAULT_DEVICE]
            self.set_status(f"Could not list devices: {e}")
        self.device_cb.configure(values=devs)
        if self.device_var.get() not in devs:
            self.device_var.set(audio.DEFAULT_DEVICE)

    def _toggle_overlay(self):
        self.cfg["overlay"] = self.overlay_var.get()
        self.overlay.show() if self.overlay_var.get() else self.overlay.hide()

    def set_status(self, msg):
        self.status.configure(text=msg)

    # ------------------------------------------------------- API settings
    def api_settings(self):
        p = translators.BY_NAME.get(self.prov_var.get(), translators.GoogleFree)
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{p.name} settings")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both")
        ttk.Label(frm, text=p.description, wraplength=420).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        if not p.fields:
            ttk.Label(frm, text="This translator needs no settings.").grid(row=1, column=0, sticky="w")
        vars_ = {}
        keys = self.cfg.setdefault("keys", {})
        for i, (key, label, secret) in enumerate(p.fields, start=1):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=keys.get(key, ""))
            e = ttk.Entry(frm, textvariable=v, width=44, show="•" if secret else "")
            e.grid(row=i, column=1, sticky="ew", pady=3, padx=6)
            if secret:
                ttk.Checkbutton(frm, text="show", command=lambda e=e: e.configure(show="" if e.cget("show") else "•")
                                ).grid(row=i, column=2)
            vars_[key] = v
        row = len(p.fields) + 1
        result = ttk.Label(frm, text="", wraplength=420)
        result.grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        def save():
            for k, v in vars_.items():
                keys[k] = v.get().strip()
            config.save(self._collect())

        def test():
            save()
            try:
                tgt = languages.NAME_TO_CODE.get(self.tgt_var.get(), "en")
                out = translators.create(p.name, keys).translate("Hello, this is a test.", "en", tgt)
                result.configure(text=f"OK: {out}", foreground="#0a7d32")
            except Exception as e:
                result.configure(text=f"Failed: {e}", foreground="#b00020")

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Test", command=test).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=lambda: (save(), dlg.destroy())).pack(side="left")
        ttk.Label(frm, text=f"Keys are stored locally in {config.CONFIG_PATH}", foreground="#888",
                  wraplength=420).grid(row=row + 2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        dlg.grab_set()

    # ------------------------------------------------------- start / stop
    def _collect(self):
        self.cfg.update(
            device=self.device_var.get(), source=self.src_var.get(), target=self.tgt_var.get(),
            provider=self.prov_var.get(), model=self.model_var.get().split(" - ")[0],
            show_partials=self.partial_var.get(), overlay=self.overlay_var.get(),
            overlay_show_source=self.showsrc_var.get(),
        )
        return self.cfg

    def toggle(self):
        if self.pipeline:
            self.stop()
        else:
            self.start()

    def start(self):
        cfg = self._collect()
        config.save(cfg)
        tr = translators.create(cfg["provider"], cfg.get("keys", {}))
        if tr.whisper_task == "translate" and languages.base(languages.NAME_TO_CODE.get(cfg["target"], "en")) != "en":
            messagebox.showwarning("Whisper offline", "Whisper offline can only translate into English.\n"
                                   "Set 'To' to English or pick another translator.")
            return
        if tr.whisper_task == "translate" and cfg["model"] == "large-v3-turbo":
            messagebox.showwarning("Whisper offline", "large-v3-turbo can't translate. Pick small, medium or large-v3.")
            return
        cap = audio.AudioCapture(cfg["device"])
        self.capture = cap
        self.pipeline = Pipeline(
            cfg, tr,
            on_result=lambda r: self.events.put(("result", r)),
            on_status=lambda s: self.events.put(("status", s)),
            audio_source=cap,
        )
        self.pipeline.start()
        self.start_btn.configure(text="Stop")
        self.set_status("Starting...")

    def stop(self):
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
        self.start_btn.configure(text="Start translating")
        self.meter["value"] = 0
        self._drop_partial()
        self.set_status("Stopped.")

    # ------------------------------------------------------- results
    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    if self.pipeline or payload.startswith("Error"):
                        self.set_status(payload)
                elif kind == "result":
                    self._show(payload)
        except queue.Empty:
            pass
        if self.pipeline and getattr(self, "capture", None):
            self.meter["value"] = min(100, self.capture.level * 400)
        self.root.after(50, self._poll)

    def _drop_partial(self):
        self.text.configure(state="normal")
        if self.text.compare("pstart", "<", "end-1c"):
            self.text.delete("pstart", "end-1c")
        self.text.configure(state="disabled")

    def _show(self, r):
        if r.error and not r.source:  # startup failure
            self.stop()
            self.set_status(f"Error: {r.error}")
            messagebox.showerror("Live Audio Translator", r.error)
            return
        if self.pipeline is None:
            return
        shown = r.translation or r.source
        self._drop_partial()
        t = self.text
        t.configure(state="normal")
        lang = f" [{r.source_lang}]" if r.source_lang else ""
        if r.partial:
            t.mark_set("pstart", "end-1c")
            t.insert("end", shown + "\n", "partial")
        else:
            if r.source != shown:
                t.insert("end", f"{time.strftime('%H:%M:%S')}{lang}  {r.source}\n", "src")
            t.insert("end", shown + "\n", "tr")
            if r.error:
                t.insert("end", f"Translation failed: {r.error}\n", "err")
            t.insert("end", "\n")
            t.mark_set("pstart", "end-1c")
            self.lines.append((time.strftime("%H:%M:%S"), r.source_lang or "", r.source, r.translation))
        t.see("end")
        t.configure(state="disabled")
        if r.error:
            self.set_status(f"Translation error: {r.error}")
        sub = r.source if r.source != shown else ""
        self.overlay.set_text(shown, sub, partial=r.partial)

    def clear(self):
        self.lines.clear()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.mark_set("pstart", "end-1c")
        self.text.configure(state="disabled")

    def save_transcript(self):
        if not self.lines:
            messagebox.showinfo("Save transcript", "Nothing to save yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")],
                                            initialfile=time.strftime("transcript_%Y-%m-%d_%H%M.txt"))
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for ts, lang, src, tr in self.lines:
                f.write(f"[{ts}] ({lang}) {src}\n           -> {tr}\n\n")
        self.set_status(f"Saved {path}")

    def on_close(self):
        self.stop()
        self.overlay.hide()
        self.cfg["window_geometry"] = self.root.geometry()
        config.save(self._collect())
        self.root.destroy()


def main():
    try:
        ctypes = __import__("ctypes")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
