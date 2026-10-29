"""Silent end-to-end test: feeds a WAV file through the real pipeline (no speakers involved).

    .venv\\Scripts\\python tests\\test_pipeline.py [wav] [target language name] [provider name]
"""

import os
import queue
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lat import config, translators  # noqa: E402
from lat.audio import TARGET_RATE, resample  # noqa: E402
from lat.engine import Pipeline  # noqa: E402


class FileSource:
    """Stands in for AudioCapture: plays a file into the queue at `speed`x real time."""

    def __init__(self, audio, speed=2.0):
        self.audio = audio
        self.speed = speed
        self.queue = queue.Queue()
        self.finished = False
        self.device_label = "test file"

    def start(self):
        def run():
            step = int(TARGET_RATE * 0.05)
            for i in range(0, len(self.audio), step):
                self.queue.put(self.audio[i:i + step])
                time.sleep(0.05 / self.speed)
            self.finished = True
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        pass


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    here = os.path.dirname(os.path.abspath(__file__))
    wav = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "sample_en.wav")
    target = sys.argv[2] if len(sys.argv) > 2 else "Spanish"
    provider = sys.argv[3] if len(sys.argv) > 3 else translators.GoogleFree.name

    with wave.open(wav) as w:
        assert w.getsampwidth() == 2, "16-bit WAV expected"
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        speech = resample(x, w.getframerate()).astype(np.float32)
    gap = np.zeros(int(1.5 * TARGET_RATE), dtype=np.float32)
    # two utterances separated by silence, with light background noise
    audio = np.concatenate([gap, speech, gap, speech, gap]).astype(np.float32)
    audio += np.random.default_rng(0).normal(0, 0.002, len(audio)).astype(np.float32)

    cfg = config.load()
    cfg.update(source="Auto-detect", target=target, provider=provider, model=os.environ.get("MODEL", "small"))
    results = []
    done = threading.Event()

    def on_result(r):
        results.append(r)
        tag = "partial" if r.partial else "FINAL  "
        print(f"[{tag}] #{r.seq} ({r.source_lang}) {r.source!r}\n          -> {r.translation!r}"
              + (f"  ERROR: {r.error}" if r.error else ""), flush=True)

    src = FileSource(audio)
    t0 = time.time()
    p = Pipeline(cfg, translators.create(provider, cfg.get("keys", {})), on_result,
                 lambda s: print("status:", s, flush=True), audio_source=src)
    p.start()
    p.threads[0].join()
    p.threads[1].join(timeout=15)
    finals = [r for r in results if not r.partial]
    print(f"\n{len(finals)} final segments, {len(results) - len(finals)} partials, {time.time() - t0:.1f}s "
          f"for {len(audio) / TARGET_RATE:.1f}s of audio")
    # each copy of the clip may be split at its inner sentence pause -> 2..4 finals
    ok = 2 <= len(finals) <= 4 and all(r.translation and not r.error for r in finals)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
