"""CamFlow — hold-to-dictate menu bar app.

Hold the hotkey (right Option by default), speak, release. The audio is
transcribed locally with Whisper and the text is pasted into whichever app
has focus.
"""

from __future__ import annotations

import threading
import time

from pynput import keyboard

from .cleanup import clean_transcript
from .config import Config
from .recorder import SAMPLE_RATE, Recorder
from .stats import Stats
from .transcriber import Transcriber
from .typer import paste_text

ICONS = {
    "loading": "🎤…",
    "idle": "🎤",
    "recording": "🔴",
    "transcribing": "⏳",
    "paused": "⏸",
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

    # A press shorter than this is a "tap"; longer is push-to-talk.
    TAP_MAX = 0.35
    # Two taps within this window count as a double-tap (toggles lock mode).
    DOUBLE_TAP_GAP = 0.4

    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = "loading"
        self.last_transcript = ""
        self.stats = Stats()
        self.locked = False  # hands-free mode (double-tap to toggle)
        self._press_time = 0.0
        self._last_tap = 0.0
        self._ignore_next_release = False
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
        if key != self._hotkey or self.state in ("loading", "transcribing", "paused"):
            return
        # In hands-free (locked) mode, a press ends recording and transcribes.
        if self.locked:
            self.locked = False
            self._ignore_next_release = True
            self._finish(self._recorder.stop())
            return
        if self.state == "idle":
            self.state = "recording"
            self._press_time = time.time()
            self._recorder.start()

    def _on_release(self, key) -> None:
        if key != self._hotkey:
            return
        if self._ignore_next_release:
            self._ignore_next_release = False
            return
        if self.state != "recording" or self.locked:
            return
        now = time.time()
        hold = now - self._press_time
        audio = self._recorder.stop()
        if hold >= self.TAP_MAX:
            # Push-to-talk: held down, so transcribe what was said.
            self._last_tap = 0.0
            self._finish(audio)
        elif now - self._last_tap <= self.DOUBLE_TAP_GAP:
            # Double-tap → enter hands-free mode: record until tapped again.
            self._last_tap = 0.0
            self.locked = True
            self.state = "recording"
            self._recorder.start()
        else:
            # Single quick tap: discard the blip, remember it for double-tap.
            self._last_tap = now
            self.state = "idle"

    def _finish(self, audio) -> None:
        if len(audio) < self.config.min_duration * SAMPLE_RATE:
            self.state = "idle"
            return
        self.state = "transcribing"
        threading.Thread(
            target=self._transcribe_and_paste, args=(audio,), daemon=True
        ).start()

    @property
    def level(self) -> float:
        """Current 0..1 voice level (only meaningful while recording)."""
        return self._recorder.level

    def toggle_pause(self) -> bool:
        """Pause/resume dictation; returns True if now paused."""
        if self.state == "paused":
            self.state = "idle"
            return False
        if self.state == "recording":
            self._recorder.stop()
        self.locked = False
        if self.state in ("idle", "recording"):
            self.state = "paused"
        return self.state == "paused"

    def _transcribe_and_paste(self, audio) -> None:
        try:
            start = time.time()
            text = self._transcriber.transcribe(audio)
            text = clean_transcript(text, self.config)
            print(f"({time.time() - start:.1f}s) {text!r}")
            if text:
                self.last_transcript = text
                paste_text(text, restore_clipboard=self.config.restore_clipboard)
                self.stats.record(text, len(audio) / SAMPLE_RATE)
        except Exception as exc:
            print(f"transcription failed: {exc}")
        finally:
            self.state = "idle"


def run_menu_bar(dictation: Dictation, dashboard_url: str | None) -> None:
    import webbrowser

    import rumps

    try:
        from .overlay import Overlay

        overlay = Overlay(dictation.config)
    except Exception as exc:
        print(f"on-screen indicator disabled: {exc}")
        overlay = None

    class CamFlowApp(rumps.App):
        def __init__(self) -> None:
            hotkey_label = HOTKEY_LABELS.get(
                dictation.config.hotkey, dictation.config.hotkey
            )
            self._words_item = rumps.MenuItem("0 words dictated")
            self._pause_item = rumps.MenuItem(
                "Pause dictation", callback=self._toggle_pause
            )
            menu = [
                rumps.MenuItem(f"Hold {hotkey_label} to dictate"),
                rumps.MenuItem(f"Double-tap {hotkey_label} for hands-free"),
                self._words_item,
                None,  # separator
                self._pause_item,
            ]
            if dashboard_url:
                menu.append(
                    rumps.MenuItem(
                        "Open Dashboard",
                        callback=lambda _: webbrowser.open(dashboard_url),
                    )
                )
            super().__init__(
                "CamFlow",
                title=ICONS["loading"],
                menu=menu,
                quit_button="Quit CamFlow",
            )
            # Poll dictation state from the main thread; AppKit UI updates
            # are not safe from the listener/worker threads. 25 fps keeps the
            # voice indicator animation smooth.
            self._tick = 0
            self._timer = rumps.Timer(self._refresh, 0.04)
            self._timer.start()

        def _toggle_pause(self, item) -> None:
            paused = dictation.toggle_pause()
            item.title = "Resume dictation" if paused else "Pause dictation"

        def _refresh(self, _timer) -> None:
            icon = ICONS[dictation.state]
            if self.title != icon:
                self.title = icon
            if overlay is not None:
                overlay.refresh(dictation.state, dictation.level)
            self._tick += 1
            if self._tick % 50 == 0:  # every ~2s
                summary = dictation.stats.summary()
                self._words_item.title = (
                    f"{summary['total_words']:,} words dictated "
                    f"({summary['words_today']:,} today)"
                )

    CamFlowApp().run()


def run_headless(dictation: Dictation) -> None:
    print("rumps not available — running without a menu bar icon (Ctrl+C to quit)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    from . import singleton

    if not singleton.acquire():
        print(
            "CamFlow is already running — this copy is exiting to avoid "
            "double dictation. Use the menu bar icon to control it."
        )
        return

    config = Config.load()
    dictation = Dictation(config)

    try:
        from .doctor import warn_missing_permissions

        warn_missing_permissions()
    except Exception:
        pass

    try:
        from .dashboard import start_dashboard

        dashboard_url = start_dashboard(config, dictation.stats)
    except OSError as exc:
        print(f"dashboard disabled (port {config.dashboard_port} busy?): {exc}")
        dashboard_url = None

    dictation.start()
    try:
        import rumps  # noqa: F401

        run_menu_bar(dictation, dashboard_url)
    except ImportError:
        run_headless(dictation)


if __name__ == "__main__":
    main()
