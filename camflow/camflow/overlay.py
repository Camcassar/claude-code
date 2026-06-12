"""Floating on-screen voice indicator — the "CC" bubble.

A small always-on-top circle in the bottom-left corner of the screen, shown
while dictating. It grows and shrinks with your voice level while recording
(red), and breathes gently while transcribing (gray). Hidden when idle.

Built on AppKit via PyObjC: a borderless transparent window that ignores
mouse events, joins all Spaces, and floats above full-screen apps.
"""

from __future__ import annotations

import math
import time

import AppKit
import Foundation
import objc

WINDOW_SIZE = 120  # pt, square window holding the circle + halo
MARGIN = 24  # pt from the bottom-left corner of the screen
BASE_RADIUS = 26.0
MAX_GROWTH = 18.0  # extra radius at full voice level
HALO = 10.0

RECORDING_COLOR = (0.93, 0.26, 0.21)  # red
TRANSCRIBING_COLOR = (0.45, 0.45, 0.52)  # gray


class _CircleView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_CircleView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.level = 0.0
        self.mode = "recording"
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        cx = bounds.size.width / 2.0
        cy = bounds.size.height / 2.0

        if self.mode == "transcribing":
            pulse = (math.sin(time.time() * 5.0) + 1.0) / 2.0
            radius = BASE_RADIUS + 5.0 * pulse
            r, g, b = TRANSCRIBING_COLOR
        else:
            radius = BASE_RADIUS + MAX_GROWTH * max(0.0, min(1.0, self.level))
            r, g, b = RECORDING_COLOR
        color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.95)

        halo_radius = radius + HALO
        halo = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            Foundation.NSMakeRect(
                cx - halo_radius, cy - halo_radius, 2 * halo_radius, 2 * halo_radius
            )
        )
        color.colorWithAlphaComponent_(0.25).setFill()
        halo.fill()

        circle = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            Foundation.NSMakeRect(cx - radius, cy - radius, 2 * radius, 2 * radius)
        )
        color.setFill()
        circle.fill()

        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(19),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        label = Foundation.NSString.stringWithString_("CC")
        size = label.sizeWithAttributes_(attrs)
        label.drawAtPoint_withAttributes_(
            Foundation.NSMakePoint(cx - size.width / 2.0, cy - size.height / 2.0),
            attrs,
        )


class Overlay:
    """Owns the floating window. All methods must be called on the main thread."""

    def __init__(self) -> None:
        rect = Foundation.NSMakeRect(MARGIN, MARGIN, WINDOW_SIZE, WINDOW_SIZE)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(False)
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self._view = _CircleView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 0, WINDOW_SIZE, WINDOW_SIZE)
        )
        window.setContentView_(self._view)
        self._window = window
        self._visible = False

    def refresh(self, state: str, level: float) -> None:
        if state in ("recording", "transcribing"):
            self._view.mode = state
            self._view.level = level
            self._view.setNeedsDisplay_(True)
            if not self._visible:
                self._window.orderFrontRegardless()
                self._visible = True
        elif self._visible:
            self._window.orderOut_(None)
            self._visible = False
