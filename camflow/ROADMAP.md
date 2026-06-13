# CamFlow roadmap

Cam's wishlist for the desktop app, and where each item stands.

## Done
- [x] Total dictated word count — dashboard + menu bar dropdown ("X words dictated (Y today)")
- [x] CC bubble customizable: `overlay_size`, `overlay_opacity`, `overlay_position` in `~/.camflow.json`
- [x] Dashboard password — set `dashboard_password` in `~/.camflow.json` (browser will prompt; leave username blank)
- [x] Customizable slang/words — Dictionary + Replacements editors in the dashboard
- [x] Pause control — menu bar → "Pause dictation" (icon becomes ⏸); Quit also in the menu

## Up next
- [ ] Clarify "changing the little bar down" (overlay corner is configurable now — is that it?)
- [ ] Dashboard sliders for bubble size/opacity (currently config-file only, needs app restart)
- [ ] Custom app name/branding pass
- [ ] Auto-learning slang from corrections (Wispr-style "learned words")
- [ ] Fully self-contained .app via PyInstaller (no ~/CamFlow folder dependency)
- [ ] Signed/notarized build for sharing without right-click → Open
