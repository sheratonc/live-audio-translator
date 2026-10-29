"""Speech pipeline: audio chunks -> utterance segments -> Whisper text -> translation.

Threads:
  capture (PortAudio callback) -> recognizer thread (segment + transcribe)
  -> translator thread (network calls, in order) -> on_result callback.
"""

import os
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import languages
from .audio import TARGET_RATE

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

# Phrases Whisper tends to invent on silence/music.
HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.", "thank you for watching.",
    "thank you for watching!", "please subscribe.", "bye.", "you", ".", "...",
    "subtitles by the amara.org community", "ご視聴ありがとうございました", "ご視聴ありがとうございました。",
    "字幕由amara.org社区提供", "продолжение следует...", "untertitel der amara.org-community",
    "sous-titres réalisés para la communauté d'amara.org", "¡suscríbete!", "gracias por ver el video.",
}


@dataclass
class Result:
    seq: int
    source: str
    translation: str
    source_lang: str | None
    partial: bool
    error: str | None = None


class Segmenter:
    """Energy-based utterance splitter with an adaptive noise floor.

    Cuts when there is `silence_s` of quiet after speech, or at `max_s`
    (at the quietest point of the last second so words aren't chopped).
    """

    FRAME = 480  # 30 ms at 16 kHz

    def __init__(self, silence_s=0.6, max_s=7.0, min_s=0.6, sensitivity=1.0):
        self.silence_frames = int(silence_s * TARGET_RATE / self.FRAME)
        self.max_samples = int(max_s * TARGET_RATE)
        self.min_samples = int(min_s * TARGET_RATE)
        self.sensitivity = sensitivity
        self.noise = 0.003
        self.buf = []          # list of frames in current utterance
        self.pre = []          # a little audio before speech starts
        self.quiet_run = 0
        self.voiced = 0
        self._rem = np.zeros(0, dtype=np.float32)

    @property
    def active(self):
        return bool(self.buf)

    def current(self):
        return np.concatenate(self.buf) if self.buf else np.zeros(0, dtype=np.float32)

    def push(self, x):
        """Feed audio; returns list of finished utterances (np arrays)."""
        out = []
        x = np.concatenate([self._rem, x])
        n = len(x) // self.FRAME
        self._rem = x[n * self.FRAME:]
        for i in range(n):
            f = x[i * self.FRAME:(i + 1) * self.FRAME]
            e = float(np.sqrt(np.mean(f * f)))
            thresh = max(0.004, self.noise * 2.5) / self.sensitivity
            loud = e > thresh
            if not loud:
                # track noise floor only on quiet frames
                self.noise = 0.95 * self.noise + 0.05 * e
            if not self.buf:
                self.pre.append(f)
                self.pre = self.pre[-10:]  # 300 ms pre-roll
                if loud:
                    self.buf = list(self.pre)
                    self.pre = []
                    self.quiet_run = 0
                    self.voiced = 1
                continue
            self.buf.append(f)
            if loud:
                self.quiet_run = 0
                self.voiced += 1
            else:
                self.quiet_run += 1
            total = len(self.buf) * self.FRAME
            if self.quiet_run >= self.silence_frames:
                seg = self._take(len(self.buf))
                if self.voiced * self.FRAME >= self.min_samples * 0.4 and len(seg) >= self.min_samples:
                    out.append(seg)
                self.voiced = 0
            elif total >= self.max_samples:
                out.append(self._cut_quietest())
        return out

    def _take(self, nframes):
        seg = np.concatenate(self.buf[:nframes])
        self.buf = self.buf[nframes:]
        self.quiet_run = 0
        return seg

    def _cut_quietest(self):
        look = min(len(self.buf) // 3, int(1.0 * TARGET_RATE / self.FRAME))
        energies = [float(np.mean(f * f)) for f in self.buf[-look:]] if look else []
        cut = len(self.buf) - look + int(np.argmin(energies)) + 1 if energies else len(self.buf)
        seg = self._take(cut)
        self.voiced = len(self.buf)
        return seg

    def flush(self):
        if self.buf and len(self.buf) * self.FRAME >= self.min_samples:
            return [self._take(len(self.buf))]
        self.buf = []
        return []


class Transcriber:
    def __init__(self, model_size="small", device="cpu", compute_type="int8", status=print):
        from faster_whisper import WhisperModel

        status(f"Loading speech model '{model_size}' (first time downloads it, please wait)...")
        os.makedirs(MODELS_DIR, exist_ok=True)
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=max(4, (os.cpu_count() or 8) // 2),
            download_root=MODELS_DIR,
        )

    def transcribe(self, audio, language=None, task="transcribe", fast=False):
        """Return (text, detected_language, language_probability)."""
        segments, info = self.model.transcribe(
            audio,
            language=language,
            task=task,
            beam_size=1 if fast else 4,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            without_timestamps=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.7 and s.avg_logprob < -0.8:
                continue
            parts.append(s.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if text.lower() in HALLUCINATIONS or len(text.strip(" .,!?-…")) == 0:
            text = ""
        return text, info.language, info.language_probability


class Pipeline:
    """Runs capture -> recognize -> translate. All callbacks fire on worker threads."""

    def __init__(self, settings, translator, on_result, on_status, audio_source=None):
        self.s = settings
        self.translator = translator
        self.on_result = on_result
        self.on_status = on_status
        self.audio_source = audio_source  # AudioCapture-like with .queue/.start/.stop
        self._stop = threading.Event()
        self._tq = queue.Queue()
        self._seq = 0
        self._cache = {}
        self.transcriber = None
        self.threads = []
        self._last_lang = None   # most recent confident Whisper detection
        self._recent = []
        self._locked = None
        self._finals = 0

    # -- lifecycle -------------------------------------------------------
    def start(self):
        self._stop.clear()
        t1 = threading.Thread(target=self._recognize_loop, daemon=True, name="recognizer")
        t2 = threading.Thread(target=self._translate_loop, daemon=True, name="translator")
        self.threads = [t1, t2]
        t1.start()
        t2.start()

    def stop(self):
        self._stop.set()
        self._tq.put(None)
        if self.audio_source:
            self.audio_source.stop()

    def join(self, timeout=None):
        for t in self.threads:
            t.join(timeout)

    # -- recognizer ------------------------------------------------------
    def _recognize_loop(self):
        try:
            self.transcriber = Transcriber(self.s["model"], status=self.on_status)
            if self._stop.is_set():
                return
            self.audio_source.start()
            if self._stop.is_set():
                self.audio_source.stop()
                return
        except Exception as e:  # model download / device errors
            self.on_status(f"Error: {e}")
            self.on_result(Result(0, "", "", None, False, error=str(e)))
            return

        self.on_status(f"Listening: {getattr(self.audio_source, 'device_label', '')}")
        seg = Segmenter(
            silence_s=self.s.get("silence_s", 0.6),
            max_s=self.s.get("max_segment_s", 7.0),
            sensitivity=self.s.get("sensitivity", 1.0),
        )
        src = languages.source_name_to_code(self.s["source"])
        whisper_lang = languages.to_whisper(src)
        task = "translate" if self.translator.whisper_task == "translate" else "transcribe"
        last_partial = 0.0
        last_audio = time.monotonic()
        partial_every = self.s.get("partial_interval_s", 1.2)

        while not self._stop.is_set():
            try:
                chunk = self.audio_source.queue.get(timeout=0.2)
                last_audio = time.monotonic()
            except queue.Empty:
                # Loopback delivers nothing while the PC is silent: treat as silence.
                gap = time.monotonic() - last_audio
                if seg.active and gap > 0.2:
                    chunk = np.zeros(int(gap * TARGET_RATE), dtype=np.float32)
                    last_audio = time.monotonic()
                elif getattr(self.audio_source, "finished", False) and self.audio_source.queue.empty():
                    for u in seg.flush():
                        self._emit(u, whisper_lang, task, partial=False)
                    break
                else:
                    continue

            for utt in seg.push(chunk):
                self._emit(utt, whisper_lang, task, partial=False)
                last_partial = time.monotonic()

            if (self.s.get("show_partials", True) and seg.active
                    and time.monotonic() - last_partial >= partial_every
                    and len(seg.buf) * Segmenter.FRAME > TARGET_RATE
                    and self.audio_source.queue.qsize() < 10):  # skip partials if we're behind
                self._emit(seg.current(), whisper_lang, task, partial=True)
                last_partial = time.monotonic()

        self.audio_source.stop()
        self._tq.put("DONE")

    def _emit(self, audio, whisper_lang, task, partial):
        # In auto-detect mode, detection costs ~1s per segment on CPU. Once the last few
        # segments agree, reuse that language (re-checking every few segments).
        lang_hint = whisper_lang
        if not lang_hint and (partial or (self._locked and self._finals % 6 != 0)):
            lang_hint = self._locked or self._last_lang
        try:
            text, lang, prob = self.transcriber.transcribe(audio, lang_hint, task, fast=partial)
        except Exception as e:
            self.on_status(f"Recognition error: {e}")
            return
        if not partial:
            self._finals += 1
        if not lang_hint:
            if prob >= 0.6:
                self._last_lang = lang
                self._recent = (self._recent + [lang])[-3:]
                self._locked = lang if len(self._recent) == 3 and len(set(self._recent)) == 1 else None
        if not text:
            return
        if not partial:
            self._seq += 1
        if whisper_lang:
            detected = languages.source_name_to_code(self.s["source"])
        else:
            code = lang_hint or (lang if prob >= 0.6 else None)
            detected = languages.from_whisper(code) if code else None
        self._tq.put((self._seq + (1 if partial else 0), text, detected, partial))

    # -- translator ------------------------------------------------------
    def _translate_loop(self):
        tgt = languages.NAME_TO_CODE.get(self.s["target"], "en")
        while True:
            item = self._tq.get()
            if item == "DONE" or (item is None and self._stop.is_set()):
                break
            if item is None:
                continue
            seq, text, src_lang, partial = item
            if partial and not self._tq.empty():
                continue  # a newer result is already waiting; skip stale partial
            err = None
            same = src_lang and languages.base(src_lang) == languages.base(tgt)
            if self.translator.whisper_task == "translate" or same:
                out = text
            else:
                key = (text, src_lang, tgt)
                out = self._cache.get(key)
                if out is None:
                    try:
                        out = self.translator.translate(text, src_lang, tgt)
                        self._cache[key] = out
                        if len(self._cache) > 500:
                            self._cache.clear()
                    except Exception as e:
                        err = str(e)
                        out = ""
            try:
                self.on_result(Result(seq, text, out, src_lang, partial, err))
            except Exception as e:
                self.on_status(f"Display error: {e}")
