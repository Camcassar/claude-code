"""Dictation statistics, persisted to ~/.camflow/stats.json.

Tracks total and per-day words/dictations plus recent transcripts, powering
the dashboard. Time-saved estimate assumes ~40 wpm typing vs ~130 wpm speech.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path

STATS_PATH = Path.home() / ".camflow" / "stats.json"

TYPING_WPM = 40.0
SPEAKING_WPM = 130.0
RECENT_LIMIT = 10


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "total_words": 0,
            "total_dictations": 0,
            "total_audio_seconds": 0.0,
            "daily": {},
            "recent": [],
        }
        if STATS_PATH.exists():
            try:
                self._data.update(json.loads(STATS_PATH.read_text()))
            except (OSError, json.JSONDecodeError):
                pass

    def record(self, text: str, audio_seconds: float) -> None:
        words = len(text.split())
        if not words:
            return
        with self._lock:
            d = self._data
            d["total_words"] += words
            d["total_dictations"] += 1
            d["total_audio_seconds"] += audio_seconds
            today = date.today().isoformat()
            day = d["daily"].setdefault(today, {"words": 0, "dictations": 0})
            day["words"] += words
            day["dictations"] += 1
            d["recent"] = (
                [{"time": datetime.now().strftime("%H:%M"), "text": text}]
                + d["recent"]
            )[:RECENT_LIMIT]
            self._save()

    def _save(self) -> None:
        try:
            STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATS_PATH.write_text(json.dumps(self._data, indent=2))
        except OSError as exc:
            print(f"warning: could not save stats: {exc}")

    def summary(self) -> dict:
        with self._lock:
            d = self._data
            words = d["total_words"]
            today = d["daily"].get(date.today().isoformat(), {"words": 0, "dictations": 0})
            minutes_saved = words / TYPING_WPM - words / SPEAKING_WPM
            return {
                "total_words": words,
                "total_dictations": d["total_dictations"],
                "words_today": today["words"],
                "dictations_today": today["dictations"],
                "avg_words": round(words / d["total_dictations"], 1) if d["total_dictations"] else 0,
                "minutes_saved": round(minutes_saved),
                "daily": d["daily"],
                "recent": d["recent"],
            }
