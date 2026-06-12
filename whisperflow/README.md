# WhisperFlow

A local, open-source Wispr Flow–style dictation app for macOS.

**Hold a key, talk, release** — your speech is transcribed on-device with
Whisper and typed into whatever app you're using (Slack, your editor, a
browser, anywhere). Nothing leaves your Mac.

- 🎤 Lives in the menu bar (🎤 idle, 🔴 recording, ⏳ transcribing)
- ⌥ Hold **Right Option** to dictate (configurable)
- 🍎 Apple Silicon: [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) with `whisper-large-v3-turbo` — fast and accurate
- 💻 Intel Macs: falls back to [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- 📋 Pastes the result and restores your previous clipboard

## Quick start

```bash
cd whisperflow
./run.sh
```

The first run creates a virtualenv, installs dependencies, and downloads the
Whisper model (~1.5 GB for large-v3-turbo; one time, then fully offline).
When the menu bar icon switches from `🎤…` to `🎤`, you're ready:

1. Click into any text field
2. **Hold Right Option** and speak
3. Release — the text appears

## macOS permissions (required)

macOS will prompt for, or you must grant manually, these permissions for the
app that launches WhisperFlow (e.g. **Terminal** or **iTerm**) under
**System Settings → Privacy & Security**:

| Permission | Why |
|---|---|
| **Microphone** | Record your voice |
| **Input Monitoring** | Detect the hold-to-talk hotkey globally |
| **Accessibility** | Synthesize the ⌘V keystroke that inserts text |

If the hotkey does nothing or text never appears, it's almost always a
missing permission — toggle it off/on for your terminal and restart the app.

## Configuration

Optional. Create `~/.whisperflow.json`:

```json
{
  "hotkey": "alt_r",
  "model": "",
  "language": "en",
  "backend": "auto",
  "min_duration": 0.3,
  "restore_clipboard": true,
  "replacements": {"new line": "\n", "new paragraph": "\n\n"}
}
```

| Key | Default | Notes |
|---|---|---|
| `hotkey` | `"alt_r"` | Any [pynput key name](https://pynput.readthedocs.io/en/latest/keyboard.html#pynput.keyboard.Key): `alt_r`, `cmd_r`, `ctrl_r`, `f13`… |
| `model` | auto | e.g. `mlx-community/whisper-large-v3-turbo` (MLX) or `base`/`small`/`medium` (faster-whisper). Smaller = faster, less accurate. |
| `language` | auto-detect | ISO code like `en` — setting it speeds things up slightly |
| `backend` | `auto` | `mlx`, `faster-whisper`, or `auto` (MLX on Apple Silicon) |
| `min_duration` | `0.3` | Discard accidental taps shorter than this (seconds) |
| `restore_clipboard` | `true` | Put your old clipboard back after pasting |
| `replacements` | `{}` | Spoken phrase → replacement text |

Every key can also be set via environment variable, e.g.
`WHISPERFLOW_HOTKEY=cmd_r ./run.sh`.

## Launch at login

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/path/to/whisperflow/run.sh", hidden:true}'
```

(or add `run.sh` under System Settings → General → Login Items).

## How it works

```
hold hotkey ──▶ record mic (16 kHz mono, sounddevice)
release     ──▶ transcribe locally (mlx-whisper / faster-whisper)
            ──▶ copy to clipboard, synthesize ⌘V, restore clipboard
```

| File | Role |
|---|---|
| `whisperflow/app.py` | Menu bar UI + hotkey state machine |
| `whisperflow/recorder.py` | Microphone capture |
| `whisperflow/transcriber.py` | Whisper backends |
| `whisperflow/typer.py` | Clipboard paste injection |
| `whisperflow/config.py` | `~/.whisperflow.json` + env vars |
