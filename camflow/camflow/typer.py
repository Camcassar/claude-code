"""Insert transcribed text into the frontmost app.

Puts the text on the clipboard and synthesizes Cmd+V — the same approach
Wispr Flow and most dictation tools use, because it works in every app
(including ones that reject synthetic per-character key events) and handles
unicode and newlines. The previous clipboard contents are restored afterwards.

Requires the host process (your terminal, or Python) to have Accessibility
permission in System Settings → Privacy & Security.
"""

from __future__ import annotations

import subprocess
import threading
import time

from pynput.keyboard import Controller, Key

_keyboard = Controller()


def _get_clipboard() -> str:
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout


def _set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, check=True)


def paste_text(text: str, restore_clipboard: bool = True) -> None:
    if not text:
        return
    previous = _get_clipboard() if restore_clipboard else None
    _set_clipboard(text)
    # Brief settle so the pasteboard write lands before the keystroke.
    time.sleep(0.02)
    with _keyboard.pressed(Key.cmd):
        _keyboard.press("v")
        _keyboard.release("v")
    if previous is not None:
        # Let the frontmost app consume the paste before restoring the
        # previous clipboard, on a background thread so dictation returns
        # to idle immediately instead of blocking on this delay.
        def _restore() -> None:
            time.sleep(0.15)
            _set_clipboard(previous)

        threading.Thread(target=_restore, daemon=True).start()
