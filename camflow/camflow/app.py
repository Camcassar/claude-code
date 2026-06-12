"""CamFlow — hold-to-dictate menu bar app.

Hold the hotkey (right Option by default), speak, release. The audio is
transcribed locally with Whisper and the text is pasted into whichever app
has focus.
"""

from __future__ import annotations

import threading
import time

from pynput import keyboard

from .config import Config
from .recorder import SAMPLE_RATE, Recorder
from .transcriber import Transcriber
from .typer import paste_text

ICONS = {
    "loading": "🎤…",
    "idle": "🎤",
    "recording": "🔴",
    "transcribing": "⏳",
}

HOTKEY_LABELS = {
    "alt_r": "Right ⌥ (Option)",
    "alt_l": "Left ⌥ (Option)",
    "cmd_r": "Right ⌘ (Command)",
    "ctrl_r": "Right ⌃ (Control)",
}


def _resolve_hotkey(name: str):
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        if len(name) == 1:
            return keyboard.KeyCode.from_char(name)
        raise ValueError(
            f"unknown hotkey {name!r}; use a pynput key name like "
            "'alt_r', 'cmd_r', 'ctrl_r', or 'f13'"
        )


class Dictation:
    """Hotkey listener + record/transcribe/paste pipeline (UI-agnostic)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = "loading"
        self.last_transcript = ""
        self._hotkey = _resolve_hotkey(config.hotkey)
        self._recorder = Recorder()
        self._transcriber = Transcriber(config)
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )

    def start(self) -> None:
        threading.Thread(target=self._warm_up, daemon=True).start()
        self._listener.start()

    def _warm_up(self) -> None:
        try:
            self._transcriber.warm_up()
        finally:
            if self.state == "loading":
                self.state = "idle"
        print(f"ready — hold {self.config.hotkey} to dictate")

    def _on_press(self, key) -> None:
        if key == self._hotkey and self.state == "idle":
            self.state = "recording"
            self._recorder.start()

    def _on_release(self, key) -> None:
        if key == self._hotkey and self.state == "recording":
            audio = self._recorder.stop()
            if len(audio) < self.config.min_duration * SAMPLE_RATE:
                self.state = "idle"
                return
            self.state = "transcribing"
            threading.Thread(
                target=self._transcribe_and_paste, args=(audio,), daemon=True
            ).start()

    def _transcribe_and_paste(self, audio) -> None:
        try:
            start = time.time()
            text = self._transcriber.transcribe(audio)
            print(f"({time.time() - start:.1f}s) {text!r}")
            if text:
                self.last_transcript = text
                paste_text(text, restore_clipboard=self.config.restore_clipboard)
        except Exception as exc:
            print(f"transcription failed: {exc}")
        finally:
            self.state = "idle"


def run_menu_bar(dictation: Dictation) -> None:
    import rumps

    class CamFlowApp(rumps.App):
        def __init__(self) -> None:
            hotkey_label = HOTKEY_LABELS.get(
                dictation.config.hotkey, dictation.config.hotkey
            )
            super().__init__(
                "CamFlow",
                title=ICONS["loading"],
                menu=[rumps.MenuItem(f"Hold {hotkey_label} to dictate")],
                quit_button="Quit CamFlow",
            )
            # Poll dictation state from the main thread; AppKit UI updates
            # are not safe from the listener/worker threads.
            self._timer = rumps.Timer(self._refresh, 0.2)
            self._timer.start()

        def _refresh(self, _timer) -> None:
            self.title = ICONS[dictation.state]

    CamFlowApp().run()


def run_headless(dictation: Dictation) -> None:
    print("rumps not available — running without a menu bar icon (Ctrl+C to quit)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    config = Config.load()
    dictation = Dictation(config)
    dictation.start()
    try:
        import rumps  # noqa: F401

        run_menu_bar(dictation)
    except ImportError:
        run_headless(dictation)


if __name__ == "__main__":
    main()
