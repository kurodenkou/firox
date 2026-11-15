"""
Close a screen saver at the first sign of someone at the computer: a key
press, click, scroll, touch, or the mouse moving more than a few pixels.

Used by every full-screen screen saver window (screensaver-window for the
terminal ones, fishtank). The idle watcher (swayidle) can stop them too, but
it only notices after a second without input, so this is what makes
Preview, and waking from the screen saver, feel instant.

    watch = InputWatch(on_quit)
    watch.attach(window, child_widget, ...)
"""

import os
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

GRACE = 1.0     # seconds after starting when input is ignored (the click on Preview)
SLOP = 12       # pixels the mouse may drift before it counts

MASK = (Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
        | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
        | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.TOUCH_MASK)


DEBUG = bool(os.environ.get("FIROX_SAVER_DEBUG"))


class InputWatch:
    def __init__(self, on_quit):
        self.on_quit = on_quit
        self.started = time.monotonic()
        self.pointer = None
        self.done = False

    def attach(self, *widgets):
        # Widgets with their own input window (GL areas, terminals) need the
        # events selected on them as well as on the window
        for w in widgets:
            w.add_events(MASK)
            for sig in ("key-press-event", "button-press-event", "scroll-event", "touch-event"):
                w.connect(sig, self.on_input)
            w.connect("motion-notify-event", self.on_motion)

    def settled(self):
        return time.monotonic() - self.started > GRACE

    def quit(self):
        if not self.done:
            self.done = True
            self.on_quit()

    def on_input(self, _widget, event):
        if DEBUG:
            print("saver input:", event.type.value_nick, file=sys.stderr, flush=True)
        if self.settled():
            self.quit()
        return True             # don't pass keys on to the saver program

    def on_motion(self, _widget, event):
        x, y = event.x_root, event.y_root
        if DEBUG:
            print("saver motion:", x, y, file=sys.stderr, flush=True)
        if self.pointer is None or not self.settled():
            self.pointer = (x, y)       # wherever the pointer already was
        elif abs(x - self.pointer[0]) + abs(y - self.pointer[1]) > SLOP:
            self.quit()
        return True
