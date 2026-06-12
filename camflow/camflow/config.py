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

# Fields that hold collections — edited via the dashboard, not env vars.
_COLLECTION_FIELDS = ("replacements", "dictionary")


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
    # Strip filler words ("um", "uh") from transcripts.
    remove_fillers: bool = True
    # Rewrite transcripts with Claude (grammar/intent cleanup). Requires
    # ANTHROPIC_API_KEY in the environment.
    ai_cleanup: bool = False
    ai_model: str = "claude-haiku-4-5"
    # Port for the local dashboard (http://localhost:<port>).
    dashboard_port: int = 4242
    # Words/phrases to replace in the transcript, e.g. {"new line": "\n"}.
    replacements: dict = field(default_factory=dict)
    # Names, slang, and jargon to bias Whisper towards (and AI cleanup).
    dictionary: list = field(default_factory=list)

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
                elif f.type == "int":
                    setattr(cfg, f.name, int(env))
                elif f.type == "bool":
                    setattr(cfg, f.name, env.lower() in ("1", "true", "yes"))
                elif f.name not in _COLLECTION_FIELDS:
                    setattr(cfg, f.name, env)
        return cfg

    def save_collections(self) -> None:
        """Persist dictionary/replacements edits back to ~/.camflow.json."""
        data = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        data["dictionary"] = self.dictionary
        data["replacements"] = self.replacements
        CONFIG_PATH.write_text(json.dumps(data, indent=2))
