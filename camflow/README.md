# CamFlow

Local, hold-to-dictate voice typing for macOS — a Wispr Flow–style app
that runs entirely on your Mac.

**Hold a key, talk, release** — your speech is transcribed on-device with
Whisper and typed into whatever app you're using (Slack, your editor, a
browser, anywhere). Nothing leaves your Mac.

- 🎤 Lives in the menu bar (🎤 idle, 🔴 recording, ⏳ transcribing)
- 🫧 Floating "CC" bubble in the bottom-left of the screen that pulses with
  your voice while you dictate (gray "breathing" while transcribing)
- ⌥ Hold **Right Option** to dictate (configurable)
- 🍎 Apple Silicon: [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) with `whisper-large-v3-turbo` — fast and accurate
- 💻 Intel Macs: falls back to [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- 📋 Pastes the result at your cursor and restores your previous clipboard
- 📊 Local dashboard (menu bar → Open Dashboard): words dictated, time saved,
  recent transcripts, and editors for your dictionary & replacements
- 📖 Personal dictionary: names/slang are fed to Whisper so it spells them right
- 🧹 Filler-word removal ("um", "uh") built in; optional AI cleanup via Claude
  rewrites transcripts to what you meant (set `ai_cleanup: true` + `ANTHROPIC_API_KEY`)

## Quick start

```bash
git clone https://github.com/Camcassar/CamFlow.git
cd CamFlow
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
app that launches CamFlow (e.g. **Terminal** or **iTerm**) under
**System Settings → Privacy & Security**:

| Permission | Why |
|---|---|
| **Microphone** | Record your voice |
| **Input Monitoring** | Detect the hold-to-talk hotkey globally |
| **Accessibility** | Synthesize the ⌘V keystroke that inserts text |

If the hotkey does nothing or text never appears, it's almost always a
missing permission. Diagnose with:

```bash
./run.sh --doctor
```

It checks all three permissions, triggers the macOS prompts for any that are
missing, and tells you exactly what to enable. After granting, restart your
terminal and run CamFlow again.

## Configuration

Optional. Create `~/.camflow.json`:

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
| `remove_fillers` | `true` | Strip "um"/"uh" from transcripts |
| `ai_cleanup` | `false` | Rewrite transcripts with Claude (grammar/intent). Needs `ANTHROPIC_API_KEY` |
| `ai_model` | `claude-haiku-4-5` | Model used for AI cleanup |
| `dashboard_port` | `4242` | Local dashboard at `http://localhost:<port>` |
| `replacements` | `{}` | Spoken phrase → replacement text (editable in the dashboard) |
| `dictionary` | `[]` | Names/slang to bias Whisper towards (editable in the dashboard) |

Every key can also be set via environment variable, e.g.
`CAMFLOW_HOTKEY=cmd_r ./run.sh`.

## Make it a real Mac app

```bash
./make_app.sh --install
```

This builds **CamFlow.app** and copies it to /Applications — launch it from
Spotlight like any app, no terminal needed. First launch: right-click →
**Open** (it's unsigned; that's fine for personal use). macOS will then ask
for Microphone / Input Monitoring / Accessibility for **CamFlow** itself —
grant all three once and you're done. Add it to System Settings → General →
**Login Items** to have dictation ready every time you boot.

When launched as an app, logs go to `~/.camflow/camflow.log`.

No Apple Developer account is needed for your own Mac. Signing/notarization
(the $99/yr Apple Developer Program) only matters if you distribute the app
to other people and want to avoid the right-click-to-open step.

## Launch at login

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/path/to/camflow/run.sh", hidden:true}'
```

(or add `run.sh` under System Settings → General → Login Items).

## Launch page (GitHub Pages)

`docs/index.html` is a ready-made landing page with the install command.
To put it on the web for free: GitHub repo → **Settings → Pages** →
Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save.
A minute later it's live at **https://camcassar.github.io/CamFlow/**.

## Installing for someone else

Same three commands from the Quick start on their Mac. They'll need to
grant the three permissions above for their own terminal, and the model
downloads once per machine. (For a double-clickable `.app` you'd package
this with py2app or PyInstaller — not set up yet.)

## How it works

```
hold hotkey ──▶ record mic (16 kHz mono, sounddevice)
release     ──▶ transcribe locally (mlx-whisper / faster-whisper)
            ──▶ copy to clipboard, synthesize ⌘V, restore clipboard
```

| File | Role |
|---|---|
| `camflow/app.py` | Menu bar UI + hotkey state machine |
| `camflow/recorder.py` | Microphone capture |
| `camflow/transcriber.py` | Whisper backends |
| `camflow/typer.py` | Clipboard paste injection |
| `camflow/config.py` | `~/.camflow.json` + env vars |
