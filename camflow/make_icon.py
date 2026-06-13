"""Render CamFlow.icns — the CC bubble as a macOS app icon.

Draws the same red voice bubble the app shows on screen (soft halo, red
gradient circle, bold white "CC") at every size macOS wants, then packs
them with iconutil. macOS only; run via make_app.sh.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import AppKit
import Foundation

# (filename, pixels) pairs required by iconutil.
ICONSET = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def _color(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def render_png(px: int) -> bytes:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0
    )
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)

    c = px / 2.0

    # Soft halo ring around the bubble.
    halo_r = px * 0.49
    halo = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        Foundation.NSMakeRect(c - halo_r, c - halo_r, 2 * halo_r, 2 * halo_r)
    )
    _color(0.93, 0.26, 0.21, 0.28).setFill()
    halo.fill()

    # Main bubble with a top-lit red gradient.
    main_r = px * 0.40
    circle = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        Foundation.NSMakeRect(c - main_r, c - main_r, 2 * main_r, 2 * main_r)
    )
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        _color(0.98, 0.38, 0.31), _color(0.74, 0.13, 0.10)
    )
    gradient.drawInBezierPath_angle_(circle, -90.0)

    # Subtle top highlight for depth.
    hi_r = main_r * 0.82
    highlight = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        Foundation.NSMakeRect(c - hi_r, c - hi_r * 0.35 + main_r * 0.18, 2 * hi_r, hi_r)
    )
    _color(1, 1, 1, 0.18).setFill()
    highlight.fill()

    # Bold "CC".
    attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(px * 0.32),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
    }
    label = Foundation.NSString.stringWithString_("CC")
    size = label.sizeWithAttributes_(attrs)
    label.drawAtPoint_withAttributes_(
        Foundation.NSMakePoint(c - size.width / 2.0, c - size.height / 2.0), attrs
    )

    AppKit.NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
    return bytes(png)


def main() -> int:
    out = Path(__file__).parent / "CamFlow.icns"
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "CamFlow.iconset"
        iconset.mkdir()
        for name, px in ICONSET:
            (iconset / name).write_bytes(render_png(px))
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
