"""The desktop's settings and wallpaper drawing, shared by firox-desktop and
Settings > Desktop Background.

Settings live in ~/.config/pcmanfm-qt/firox/settings.conf (the file the
desktop has always used, so firox-theme and the Glass taskbar read the same
thing), falling back to /etc/xdg/pcmanfm-qt/firox/settings.conf. [Desktop]:

    Wallpaper=/path/to/image        empty: just the colour
    WallpaperMode=zoom|fit|stretch|center|tile
    BgColor=#3a3c3f                 behind (or instead of) the picture
    FgColor=#ffffff  ShadowColor=#000000     icon labels
    DesktopShortcuts=Computer,Home,Network,Trash
    DesktopIconSize=48
"""

import configparser
import os
import shutil

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

CFG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
USER_CONF = os.path.join(CFG, "pcmanfm-qt", "firox", "settings.conf")
SYS_CONF = "/etc/xdg/pcmanfm-qt/firox/settings.conf"
MODES = [("zoom", "Fill"), ("fit", "Fit"), ("stretch", "Stretch"), ("center", "Center"), ("tile", "Tile")]
SHORTCUTS = [("Computer", "Computer"), ("Home", "Home folder"), ("Network", "Network"), ("Trash", "Recycle Bin")]
DEFAULTS = {"Wallpaper": "", "WallpaperMode": "zoom", "BgColor": "#3a3c3f", "FgColor": "#ffffff",
            "ShadowColor": "#000000", "DesktopShortcuts": "Computer,Home,Trash,Network",
            "DesktopIconSize": "48", "SingleClick": "false"}


def _parser(path):
    p = configparser.ConfigParser(interpolation=None, strict=False)
    p.optionxform = str
    try:
        p.read(path)
    except configparser.Error:
        pass
    return p


def read():
    """The [Desktop] settings (plus SingleClick from [Behavior]) as a dict."""
    out = dict(DEFAULTS)
    path = USER_CONF if os.path.exists(USER_CONF) else SYS_CONF
    p = _parser(path)
    if p.has_section("Desktop"):
        out.update(p["Desktop"])
    if p.has_option("Behavior", "SingleClick"):
        out["SingleClick"] = p["Behavior"]["SingleClick"]
    return out


def write(updates):
    """Change [Desktop] settings in the user's file (made from the system
    one the first time)."""
    if not os.path.exists(USER_CONF):
        os.makedirs(os.path.dirname(USER_CONF), exist_ok=True)
        try:
            shutil.copyfile(SYS_CONF, USER_CONF)
        except OSError:
            with open(USER_CONF, "w") as fh:
                fh.write("[Desktop]\n")
    p = _parser(USER_CONF)
    if not p.has_section("Desktop"):
        p.add_section("Desktop")
    for k, v in updates.items():
        p["Desktop"][k] = v
    with open(USER_CONF + ".tmp", "w") as fh:
        p.write(fh, space_around_delimiters=False)
    os.replace(USER_CONF + ".tmp", USER_CONF)


def shortcuts(settings):
    return [s.strip() for s in settings.get("DesktopShortcuts", "").split(",") if s.strip()]


def rgba(text, fallback="#3a3c3f"):
    c = Gdk.RGBA()
    if not c.parse(text or ""):
        c.parse(fallback)
    return c


def paint(cr, width, height, settings, cache=None, scale=1):
    """Draw the wallpaper filling width x height (the same placement as the
    Glass taskbar uses). `cache` (a dict) keeps the scaled picture; `scale`
    is the monitor's scale factor (sharp pictures on HiDPI screens)."""
    bg = rgba(settings.get("BgColor"))
    cr.set_source_rgb(bg.red, bg.green, bg.blue)
    cr.paint()
    path = settings.get("Wallpaper") or ""
    mode = settings.get("WallpaperMode") or "zoom"
    if not path or width <= 0 or height <= 0:
        return
    width, height = width * scale, height * scale     # device pixels from here
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return
    key = (path, mtime, mode, width, height)
    pix = cache.get(key) if cache is not None else None
    if pix is None:
        try:
            _fmt, pw, ph = GdkPixbuf.Pixbuf.get_file_info(path)
            if not pw or not ph:
                return
            if mode == "stretch":
                w, h = width, height
            elif mode == "fit":
                k = min(width / pw, height / ph)
                w, h = pw * k, ph * k
            elif mode in ("center", "tile"):
                w, h = pw, ph
            else:                                   # zoom: cover the screen
                k = max(width / pw, height / ph)
                w, h = pw * k, ph * k
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max(1, round(w)), max(1, round(h)), False)
        except (GLib.Error, TypeError, ValueError):
            return
        if cache is not None:
            cache.clear()
            cache[key] = pix
    w, h = pix.get_width(), pix.get_height()
    cr.save()
    cr.scale(1 / scale, 1 / scale)
    if mode == "tile":
        for x0 in range(0, width, w):
            for y0 in range(0, height, h):
                Gdk.cairo_set_source_pixbuf(cr, pix, x0, y0)
                cr.paint()
    else:
        Gdk.cairo_set_source_pixbuf(cr, pix, (width - w) / 2, (height - h) / 2)
        cr.paint()
    cr.restore()
