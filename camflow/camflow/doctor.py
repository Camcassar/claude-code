"""Diagnostics for "it's not working" — checks the three macOS permissions.

Run with:  ./run.sh --doctor   (or  python -m camflow --doctor)
"""

from __future__ import annotations

GRANT_HINT = (
    "  → System Settings → Privacy & Security → {section}: enable your "
    "terminal app (Terminal/iTerm), then RESTART the terminal and CamFlow."
)


def check_permissions(prompt: bool = False) -> dict:
    """Return {check: True/False/None}; None = could not determine."""
    results = {}
    try:
        if prompt:
            from ApplicationServices import (  # noqa: F401
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            results["accessibility"] = bool(
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            )
        else:
            from ApplicationServices import AXIsProcessTrusted

            results["accessibility"] = bool(AXIsProcessTrusted())
    except Exception:
        results["accessibility"] = None
    try:
        import Quartz

        if prompt:
            Quartz.CGRequestListenEventAccess()
        results["input_monitoring"] = bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        results["input_monitoring"] = None
    try:
        import sounddevice as sd

        results["microphone_device"] = sd.query_devices(kind="input")["name"]
    except Exception:
        results["microphone_device"] = None
    return results


def run_doctor() -> None:
    print("CamFlow doctor\n" + "=" * 40)
    results = check_permissions(prompt=True)

    mic = results["microphone_device"]
    print(f"[{'OK' if mic else 'FAIL'}] Input device: {mic or 'none found'}")
    if not mic:
        print(GRANT_HINT.format(section="Microphone"))

    im = results["input_monitoring"]
    label = {True: "OK", False: "FAIL", None: "????"}[im]
    print(f"[{label}] Input Monitoring (detects the hotkey): {im}")
    if im is False:
        print(GRANT_HINT.format(section="Input Monitoring"))

    ax = results["accessibility"]
    label = {True: "OK", False: "FAIL", None: "????"}[ax]
    print(f"[{label}] Accessibility (pastes text at your cursor): {ax}")
    if ax is False:
        print(GRANT_HINT.format(section="Accessibility"))

    print("=" * 40)
    if all(v for v in results.values()):
        print("All checks passed — CamFlow should work. If it still doesn't,")
        print("run ./run.sh and watch this terminal for errors while dictating.")
    else:
        print("Fix the FAIL items above (macOS may have just shown permission")
        print("prompts — accept them), restart your terminal, and re-run --doctor.")


def warn_missing_permissions() -> None:
    """Print startup warnings (non-blocking) for missing permissions."""
    results = check_permissions(prompt=True)
    if results["input_monitoring"] is False:
        print("WARNING: Input Monitoring permission missing — the hotkey will not work.")
        print(GRANT_HINT.format(section="Input Monitoring"))
    if results["accessibility"] is False:
        print("WARNING: Accessibility permission missing — text cannot be pasted.")
        print(GRANT_HINT.format(section="Accessibility"))
