"""
firox_popup: the frame shared by the taskbar's pop-ups (Wi-Fi, Bluetooth).

A borderless layer-shell window covering the screen; the panel sits in the
bottom-right corner above the notification area, and a click anywhere else
or Esc closes it. Styled by the theme's popup.css
(/usr/share/firox/themes/NAME/popup.css).
"""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # noqa: E402
except (ValueError, ImportError):
    GtkLayerShell = None

import firox_paths  # noqa: E402

THEMES = "/usr/share/firox/themes"


def read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def theme_css(name="popup.css"):
    theme = "classic"
    for path in (os.path.join(firox_paths.CONFIG, "firox", "theme"), "/etc/firox/theme"):
        if read(path):
            theme = read(path)
            break
    path = os.path.join(THEMES, theme, name)
    return path if os.path.exists(path) else os.path.join(THEMES, "classic", name)


def spawn(argv):
    try:
        GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH)
    except GLib.Error as err:
        print(f"popup: can't start {argv[0]}: {err.message}", file=sys.stderr)


def label(text="", cls=None, xalign=0, wrap=False):
    lab = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        lab.set_line_wrap(True)
        lab.set_max_width_chars(34)
    if cls:
        for c in cls.split():
            lab.get_style_context().add_class(c)
    return lab


class Popup(Gtk.Window):
    """Subclasses fill self.body (and self.head_extra), and refresh()."""

    def __init__(self, app, title, namespace):
        super().__init__(application=app, title=title)
        self.set_decorated(False)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(screen, self.provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if GtkLayerShell and GtkLayerShell.is_supported():
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, namespace)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self, edge, True)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)

        catcher = Gtk.EventBox()
        catcher.set_visible_window(False)
        catcher.connect("button-press-event", lambda *_: self.hide() or True)
        self.add(catcher)
        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", lambda *_: self.hide() or True)

        swallow = Gtk.EventBox()
        swallow.set_visible_window(False)
        swallow.connect("button-press-event", lambda *_: True)
        swallow.set_halign(Gtk.Align.END)
        swallow.set_valign(Gtk.Align.END)
        catcher.add(swallow)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_name("panel")
        swallow.add(box)

        head = Gtk.Box(spacing=8)
        head.set_name("panel-header")
        self.title = label(title, "title")
        head.pack_start(self.title, True, True, 0)
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_no_show_all(True)
        self.switch_handler = self.switch.connect("notify::active", self._on_switch)
        head.pack_end(self.switch, False, False, 0)
        box.pack_start(head, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.body.set_name("body")
        box.pack_start(self.body, True, True, 0)

        self.foot = Gtk.Box(spacing=4)
        self.foot.set_name("panel-footer")
        box.pack_start(self.foot, False, False, 0)

    # ------------------------------------------------------------------

    def set_switch(self, visible, active=False, tip=None):
        self.switch.set_visible(visible)
        with self.switch.handler_block(self.switch_handler):
            self.switch.set_active(active)
        self.switch.set_tooltip_text(tip)

    def _on_switch(self, sw, _pspec):
        self.on_switch(sw.get_active())

    def on_switch(self, active):
        pass

    def link(self, text, tip, callback):
        b = Gtk.Button(label=text)
        b.get_style_context().add_class("link")
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda _b: callback())
        self.foot.pack_start(b, False, False, 0)
        return b

    def on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
        return False

    def follow_app_style(self):
        """Stays running between clicks: pick up a Classic/Modern switch."""
        try:
            iface = Gio.Settings.new("org.gnome.desktop.interface")
        except Exception:  # noqa: BLE001 - schema missing
            return
        st = Gtk.Settings.get_default()
        st.set_property("gtk-theme-name", iface.get_string("gtk-theme"))
        st.set_property("gtk-icon-theme-name", iface.get_string("icon-theme"))
        st.set_property("gtk-application-prefer-dark-theme",
                        iface.get_string("color-scheme") == "prefer-dark")

    def open(self):
        self.follow_app_style()
        try:
            self.provider.load_from_path(theme_css())
        except GLib.Error as err:
            print(f"popup: {err.message}", file=sys.stderr)
        self.show_all()
        self.refresh()
        self.present()

    def refresh(self):
        pass


class Row(Gtk.Box):
    """A list entry: icon, name and detail; click it to show its buttons."""

    def __init__(self, icon, name, detail="", key=None, badge=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.key = key
        self.get_style_context().add_class("row")
        ev = Gtk.EventBox()
        ev.connect("button-press-event", self._on_click)
        top = Gtk.Box(spacing=10)
        top.get_style_context().add_class("row-top")
        self.icon = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        self.icon.set_pixel_size(22)
        top.pack_start(self.icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        texts.set_valign(Gtk.Align.CENTER)
        self.name = label(name, "name")
        self.name.set_ellipsize(3)        # Pango.EllipsizeMode.END
        self.name.set_max_width_chars(26)
        texts.pack_start(self.name, False, False, 0)
        self.detail = label(detail, "dim")
        texts.pack_start(self.detail, False, False, 0)
        if not detail:
            self.detail.set_no_show_all(True)
        top.pack_start(texts, True, True, 0)
        if badge:
            b = Gtk.Image.new_from_icon_name(badge, Gtk.IconSize.MENU)
            b.get_style_context().add_class("badge")
            top.pack_end(b, False, False, 0)
        ev.add(top)
        self.pack_start(ev, False, False, 0)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.actions.get_style_context().add_class("row-actions")
        self.actions.set_no_show_all(True)
        self.pack_start(self.actions, False, False, 0)
        self.on_expand = None
        self.on_collapse = None

    def _on_click(self, _w, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            opening = not self.actions.get_visible()
            self.set_expanded(opening)
            if not opening and self.on_collapse:
                self.on_collapse(self)
        return True

    def set_expanded(self, on):
        if on and self.on_expand:
            self.on_expand(self)
        self.actions.set_visible(on)
        ctx = self.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("expanded")
        if on:
            for w in self.actions.get_children():
                w.show_all()

    def button_row(self, *buttons):
        box = Gtk.Box(spacing=6)
        box.set_halign(Gtk.Align.END)
        for text, cb, *cls in buttons:
            b = Gtk.Button(label=text)
            if cls:
                b.get_style_context().add_class(cls[0])
            b.connect("clicked", lambda _b, cb=cb: cb())
            box.pack_start(b, False, False, 0)
        self.actions.pack_start(box, False, False, 0)
        return box


def scroller(child, max_height=300):
    sc = Gtk.ScrolledWindow()
    sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sc.set_propagate_natural_height(True)
    sc.set_max_content_height(max_height)
    sc.add(child)
    return sc
