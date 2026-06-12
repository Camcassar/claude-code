"""Speech-to-text via local Whisper models.

Two backends, picked automatically:
  - mlx-whisper: Apple Silicon, runs on the GPU/Neural Engine via MLX. Fast.
  - faster-whisper: CTranslate2-based, works on Intel Macs (and anywhere else).

The first transcription downloads the model weights (a few hundred MB to
~1.5 GB depending on the model), after which everything is offline.
"""

from __future__ import annotations

import platform
import sys

import numpy as np

from .config import DEFAULT_FASTER_WHISPER_MODEL, DEFAULT_MLX_MODEL, Config


class Transcriber:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._backend = self._pick_backend(config.backend)
        self._model = None  # lazily loaded (faster-whisper only)
        if self._backend == "mlx":
            self._model_name = config.model or DEFAULT_MLX_MODEL
        else:
            self._model_name = config.model or DEFAULT_FASTER_WHISPER_MODEL
        print(f"transcriber: backend={self._backend} model={self._model_name}")

    @staticmethod
    def _pick_backend(preference: str) -> str:
        if preference in ("mlx", "faster-whisper"):
            return preference
        if sys.platform == "darwin" and platform.machine() == "arm64":
            try:
                import mlx_whisper  # noqa: F401

                return "mlx"
            except ImportError:
                pass
        return "faster-whisper"

    def warm_up(self) -> None:
        """Load the model (and download weights if needed) ahead of first use."""
        self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        language = self._config.language or None
        # Biasing Whisper with the user's dictionary (names, slang, jargon)
        # makes it far more likely to spell those words correctly.
        prompt = None
        if self._config.dictionary:
            prompt = "Glossary: " + ", ".join(self._config.dictionary) + "."
        if self._backend == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self._model_name,
                language=language,
                initial_prompt=prompt,
            )
            text = result["text"]
        else:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self._model_name, compute_type="int8")
            segments, _info = self._model.transcribe(
                audio, language=language, initial_prompt=prompt
            )
            text = "".join(segment.text for segment in segments)
        return text.strip()
