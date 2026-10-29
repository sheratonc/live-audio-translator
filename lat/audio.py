"""Capture what the PC is playing (WASAPI loopback) or a microphone, as 16 kHz mono float32."""

import queue

import numpy as np
import pyaudiowpatch as pyaudio

TARGET_RATE = 16000
DEFAULT_DEVICE = "Default output (what you hear)"


def list_devices():
    """Return capture device names: default loopback first, then loopbacks, then mics."""
    p = pyaudio.PyAudio()
    try:
        names = [DEFAULT_DEVICE]
        for d in p.get_loopback_device_info_generator():
            names.append(f"[PC audio] {d['name'].replace(' [Loopback]', '')}")
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] == wasapi and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                names.append(f"[Mic] {d['name']}")
        return names
    finally:
        p.terminate()


def _resolve(p, name):
    if not name or name == DEFAULT_DEVICE:
        return p.get_default_wasapi_loopback()
    if name.startswith("[PC audio] "):
        want = name[len("[PC audio] "):]
        for d in p.get_loopback_device_info_generator():
            if d["name"].replace(" [Loopback]", "") == want:
                return d
    if name.startswith("[Mic] "):
        want = name[len("[Mic] "):]
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] == wasapi and d["name"] == want and not d.get("isLoopbackDevice"):
                return d
    raise RuntimeError(f"Audio device not found: {name}. Click Refresh and pick it again.")


def resample(x, src_rate, dst_rate=TARGET_RATE):
    if src_rate == dst_rate or len(x) == 0:
        return x
    if src_rate % dst_rate == 0:
        # Integer ratio (48k/16k = 3): box low-pass then decimate.
        f = src_rate // dst_rate
        n = len(x) // f * f
        return x[:n].reshape(-1, f).mean(axis=1)
    n_out = int(round(len(x) * dst_rate / src_rate))
    return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)


class AudioCapture:
    """Background capture; read 16 kHz mono chunks from .queue."""

    def __init__(self, device_name=None):
        self.device_name = device_name
        self.queue = queue.Queue()
        self.level = 0.0  # last chunk RMS, for the UI meter
        self.device_label = ""

    def start(self):
        self._p = pyaudio.PyAudio()
        dev = _resolve(self._p, self.device_name)
        self.device_label = dev["name"]
        self._channels = max(1, int(dev["maxInputChannels"]))
        self._rate = int(dev["defaultSampleRate"])
        self._stream = self._p.open(
            format=pyaudio.paFloat32,
            channels=self._channels,
            rate=self._rate,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=int(self._rate * 0.05),
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def _callback(self, in_data, frame_count, time_info, status):
        x = np.frombuffer(in_data, dtype=np.float32)
        if self._channels > 1:
            x = x.reshape(-1, self._channels).mean(axis=1)
        x = resample(x, self._rate).astype(np.float32)
        self.level = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        self.queue.put(x)
        return (None, pyaudio.paContinue)

    def stop(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._p.terminate()
        except Exception:
            pass
