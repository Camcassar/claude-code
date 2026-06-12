"""Microphone capture.

Records mono 16 kHz float32 audio (what Whisper expects) while the hotkey is
held. Uses sounddevice (PortAudio), which works with any macOS input device.
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        # Smoothed 0..1 voice level, read by the on-screen indicator.
        self.level = 0.0

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._frames = []
        self.level = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time, status) -> None:
        if status:
            print(f"audio status: {status}")
        rms = float(np.sqrt(np.mean(np.square(indata))))
        # Fast attack, slow decay so the indicator follows speech naturally.
        self.level = max(min(1.0, rms * 14.0), self.level * 0.82)
        with self._lock:
            self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio as a 1-D float32 array."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames).flatten()
