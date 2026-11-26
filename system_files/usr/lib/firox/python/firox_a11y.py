"""Names for screen readers (Narrator) on Firox's icon-only buttons.

Many of Firox's controls are a picture with a tooltip: the tray icons, Rune's
buttons, the taskbar's moon and shield. GTK gives a screen reader the
tooltip as a description at most, so those read as "button" or nothing.
watch(window) gives every such widget its tooltip as its accessible name,
for widgets added later too, and keeps it up to date when the tooltip
changes (the battery's "82%, 3 hours left").
"""

import unicodedata

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

KINDS = (Gtk.Button, Gtk.EventBox, Gtk.Image, Gtk.Switch, Gtk.Scale, Gtk.ComboBox, Gtk.DrawingArea)


def _words(text):
    """Text a person would read: letters or digits. (The taskbar draws its
    icons as icon-font characters in labels; read aloud they're noise.)"""
    return any(unicodedata.category(ch)[0] in "LN" for ch in text or "")


def _name(widget, *_):
    tip = widget.get_tooltip_text()
    acc = widget.get_accessible()
    # (a plain text button reads its own label; anything else built from
    # icons and bits of text, like the battery's "82%", reads its tooltip)
    if tip and acc is not None and not (isinstance(widget, Gtk.Button) and _words(widget.get_label())):
        acc.set_name(tip.split("\n")[0])


def _visit(widget):
    if getattr(widget, "_firox_a11y", False):
        return
    widget._firox_a11y = True
    if isinstance(widget, KINDS):
        _name(widget)
        widget.connect("notify::tooltip-text", _name)
    if isinstance(widget, Gtk.Container):
        widget.connect("add", lambda _c, child: _visit(child))
        for c in widget.get_children():
            _visit(c)


def watch(window):
    """Name window's icon-only widgets now and as they come and go."""
    _visit(window)
