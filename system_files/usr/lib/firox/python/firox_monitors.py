"""Which monitor is the main display (Settings > Display).

With more than one monitor, the main display has the full taskbar (tray
icons and clock), the Desktop Gadgets sidebar and notification pop-ups; the
others extend the desktop. The choice is remembered per set of connected
monitors (e.g. laptop alone vs. docked) in ~/.config/firox/main-display, as
JSON {"DP-1,eDP-1": "DP-1"}. Without a choice, the monitor at the top left
of the layout is the main one.

Used by firox-panel, firox-gadgets, firox-displays and the Control Panel.
"""

import json
import os
import subprocess

CFG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CONF = os.path.join(CFG, "firox", "main-display")


def outputs():
    """Connected monitors from wlr-randr: dicts with name, description,
    enabled, x, y (logical position). [] if wlr-randr isn't usable."""
    try:
        out = subprocess.run(["wlr-randr", "--json"], capture_output=True, text=True,
                             timeout=5, check=True).stdout
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    result = []
    for o in data:
        pos = o.get("position") or {}
        cur = next((m for m in o.get("modes", []) if m.get("current")), None)
        scale = o.get("scale") or 1.0
        w, h = (cur["width"], cur["height"]) if cur else (0, 0)
        if (o.get("transform") or "normal") in ("90", "270", "flipped-90", "flipped-270"):
            w, h = h, w
        result.append({"name": o.get("name", ""),
                       "description": o.get("description") or o.get("model") or o.get("name", ""),
                       "make": o.get("make") or "", "model": o.get("model") or "",
                       "enabled": bool(o.get("enabled")),
                       "x": pos.get("x", 0), "y": pos.get("y", 0),
                       # logical size, as the desktop lays it out
                       "width": round(w / scale), "height": round(h / scale),
                       "mode": f"{cur['width']}\u00d7{cur['height']}" if cur else ""})
    return result


def profile_key(outs):
    """The set of connected monitors (same key as firox-displays uses)."""
    return ",".join(sorted(o["name"] for o in outs))


def load():
    try:
        with open(CONF) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def default_main(outs):
    on = [o for o in outs if o["enabled"]]
    if not on:
        return None
    return min(on, key=lambda o: (o["x"], o["y"]))["name"]


def main_name(outs=None):
    """The main display's connector name (e.g. "DP-1"), or None."""
    if outs is None:
        outs = outputs()
    chosen = load().get(profile_key(outs))
    if any(o["name"] == chosen and o["enabled"] for o in outs):
        return chosen
    return default_main(outs)


def main_output(outs=None):
    """The main display's dict from outputs(), or None."""
    if outs is None:
        outs = outputs()
    name = main_name(outs)
    return next((o for o in outs if o["name"] == name), None)


def set_main(name, outs=None):
    """Make `name` the main display for the monitors connected now."""
    if outs is None:
        outs = outputs()
    if not any(o["name"] == name for o in outs):
        raise ValueError(f"no monitor called {name}")
    data = load()
    data[profile_key(outs)] = name
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF + ".tmp", "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(CONF + ".tmp", CONF)


def numbered(outs=None):
    """Enabled monitors numbered left to right, top to bottom, as Settings >
    Display and Identify show them: [(1, output), (2, output), ...]."""
    if outs is None:
        outs = outputs()
    on = sorted((o for o in outs if o["enabled"]), key=lambda o: (o["x"], o["y"]))
    return list(enumerate(on, 1))


# ---- GTK side: which GdkMonitor is it ------------------------------------

def gdk_monitors(display):
    return [display.get_monitor(i) for i in range(display.get_n_monitors())]


def gdk_monitor_for(display, output):
    """The GdkMonitor showing `output` (matched by logical position)."""
    if output is None:
        return None
    for m in gdk_monitors(display):
        g = m.get_geometry()
        if (g.x, g.y) == (output["x"], output["y"]):
            return m
    return None


def main_gdk_monitor(display, outs=None):
    """The main display as a GdkMonitor. Falls back to the top-left monitor
    when wlr-randr can't tell (so there's always exactly one)."""
    mons = gdk_monitors(display)
    if not mons:
        return None
    m = gdk_monitor_for(display, main_output(outs))
    if m is not None:
        return m
    return min(mons, key=lambda m: (m.get_geometry().x, m.get_geometry().y))
