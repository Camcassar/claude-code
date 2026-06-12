"""Configuration for CamFlow.

Settings are read from a JSON file at ~/.camflow.json, with environment
variables (CAMFLOW_*) taking precedence. Everything has a sensible
default, so no config is required to get started.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_PATH = Path.home() / ".camflow.json"

# Default Whisper models per backend. The MLX turbo model is fast and accurate
# on Apple Silicon; "base" keeps Intel Macs responsive with faster-whisper.
DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_FASTER_WHISPER_MODEL = "base"


@dataclass
class Config:
    # Key held down to dictate. Any pynput key name works: "alt_r" (right
    # option), "cmd_r" (right command), "ctrl_r", "f13", etc.
    hotkey: str = "alt_r"
    # Whisper model. Empty string means "pick the default for the backend".
    model: str = ""
    # ISO language code (e.g. "en", "fr"). Empty string = auto-detect.
    language: str = ""
    # Transcription backend: "auto", "mlx", or "faster-whisper".
    backend: str = "auto"
    # Recordings shorter than this (seconds) are discarded as accidental taps.
    min_duration: float = 0.3
    # Restore the previous clipboard contents after pasting.
    restore_clipboard: bool = True
    # Words/phrases to replace in the transcript, e.g. {"new line": "\n"}.
    replacements: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                print(f"warning: could not read {CONFIG_PATH}: {exc}")
                data = {}
            for f in fields(cls):
                if f.name in data:
                    setattr(cfg, f.name, data[f.name])
        for f in fields(cls):
            env = os.environ.get(f"CAMFLOW_{f.name.upper()}")
            if env is not None:
                if f.type == "float":
                    setattr(cfg, f.name, float(env))
                elif f.type == "bool":
                    setattr(cfg, f.name, env.lower() in ("1", "true", "yes"))
                elif f.name != "replacements":
                    setattr(cfg, f.name, env)
        return cfg
