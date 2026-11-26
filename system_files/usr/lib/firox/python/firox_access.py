"""Ease of Access for Firox: the screen reader (Narrator, which is Orca), the
on-screen keyboard (firox-osk), high contrast, text size, reduced motion
and, through firox-input, the cursor size and the magnifier.

Settings are kept in ~/.config/firox/access.conf. Most take effect at once
(gsettings, which GTK, libadwaita and Firefox follow); the cursor size and
the magnifier's shape are labwc settings, applied by firox-input.
`firox-access login` (labwc autostart) puts everything back at each login.
"""

import os
import shutil
import subprocess
import time

import firox_paths

CONF = os.path.join(firox_paths.CONFIG, "firox", "access.conf")
DEFAULTS = {
    "NARRATOR": "0",        # start Narrator at login
    "KEYBOARD": "0",        # show the on-screen keyboard at login
    "CONTRAST": "0",        # high contrast apps
    "TEXT": "1.0",          # text size: 1.0 .. 2.0
    "MOTION": "1",          # 0: reduce motion (no animations)
}
TEXT_SIZES = [("1.0", "Normal"), ("1.25", "Large"), ("1.5", "Larger"), ("1.75", "Largest"), ("2.0", "Huge")]
CURSOR_SIZES = [(24, "Normal"), (32, "Large"), (48, "Larger"), (64, "Largest"), (96, "Huge")]
IFACE = "org.gnome.desktop.interface"
A11Y_APPS = "org.gnome.desktop.a11y.applications"
A11Y_IFACE = "org.gnome.desktop.a11y.interface"


def load():
    conf = dict(DEFAULTS)
    try:
        with open(CONF) as fh:
            for line in fh:
                k, sep, v = line.strip().partition("=")
                if sep and k in DEFAULTS:
                    conf[k] = v.strip()
    except OSError:
        pass
    return conf


def save(conf):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF + ".tmp", "w") as fh:
        fh.write("# Ease of Access (Settings > Ease of Access, or firox-access)\n")
        for k in DEFAULTS:
            fh.write(f"{k}={conf.get(k, DEFAULTS[k])}\n")
    os.replace(CONF + ".tmp", CONF)


def setting(key, value):
    conf = load()
    conf[key] = value
    save(conf)


def gset(schema, key, value):
    if shutil.which("gsettings"):
        subprocess.run(["gsettings", "set", schema, key, value], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)


def gget(schema, key):
    try:
        return subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True,
                              timeout=5).stdout.strip().strip("'")
    except (OSError, subprocess.SubprocessError):
        return ""


def spawn(argv):
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def running(name):
    return subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", name], stdout=subprocess.DEVNULL,
                          check=False).returncode == 0


# ---------------------------------------------------------------- Narrator --

def narrator_on():
    """Orca, reading out what's on screen. GTK apps start talking to it once
    the screen-reader setting is on."""
    gset(IFACE, "toolkit-accessibility", "true")
    gset(A11Y_APPS, "screen-reader-enabled", "true")
    if not running("orca"):
        return spawn(["orca", "--replace"])
    return True


def narrator_off():
    gset(A11Y_APPS, "screen-reader-enabled", "false")
    me = str(os.getuid())
    subprocess.run(["pkill", "-u", me, "-x", "orca"], check=False)
    for _ in range(30):                     # (Orca says goodbye first: up to 3 s)
        if not running("orca"):
            return
        time.sleep(0.1)
    subprocess.run(["pkill", "-KILL", "-u", me, "-x", "orca"], check=False)


def narrator_toggle():
    if running("orca"):
        narrator_off()
        return False
    narrator_on()
    return True


# ------------------------------------------------------ on-screen keyboard --

def keyboard(cmd="toggle"):
    return spawn(["firox-osk", f"--{cmd}"])


# ------------------------------------------- contrast, text size, motion --

def contrast(on):
    """High contrast apps: firox-theme's app style picks the HighContrast GTK
    theme while this is on (and keeps it across look switches)."""
    setting("CONTRAST", "1" if on else "0")
    gset(A11Y_IFACE, "high-contrast", "true" if on else "false")
    mode = subprocess.run(["firox-theme", "apps"], capture_output=True, text=True,
                          check=False).stdout.strip() or "auto"
    subprocess.run(["firox-theme", "apps", mode], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


def text_size(factor):
    factor = min(2.0, max(1.0, float(factor)))
    setting("TEXT", f"{factor:g}")
    gset(IFACE, "text-scaling-factor", f"{factor:g}")


def motion(on):
    setting("MOTION", "1" if on else "0")
    gset(IFACE, "enable-animations", "true" if on else "false")


def motion_on():
    """Animations wanted? (Rune and others check.)"""
    return load()["MOTION"] != "0"


def cursor_size(px):
    subprocess.run(["firox-input", "set", f"CURSOR_SIZE={int(px)}"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)


def magnifier(full=None, scale=None):
    sets = []
    if full is not None:
        sets.append(f"MAGNIFIER_FULL={'true' if full else 'false'}")
    if scale is not None:
        sets.append(f"MAGNIFIER_SCALE={float(scale):g}")
    if sets:
        subprocess.run(["firox-input", "set", *sets], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)


def login():
    """At login: everything as it was left."""
    conf = load()
    gset(IFACE, "text-scaling-factor", conf["TEXT"])
    gset(IFACE, "enable-animations", "false" if conf["MOTION"] == "0" else "true")
    gset(A11Y_IFACE, "high-contrast", "true" if conf["CONTRAST"] == "1" else "false")
    if conf["NARRATOR"] == "1":
        narrator_on()
    else:
        gset(A11Y_APPS, "screen-reader-enabled", "false")
    if conf["KEYBOARD"] == "1":
        keyboard("show")


def status():
    conf = load()
    return {"narrator": running("orca"), "narrator_at_login": conf["NARRATOR"] == "1",
            "keyboard": running("firox-osk"), "keyboard_at_login": conf["KEYBOARD"] == "1",
            "contrast": conf["CONTRAST"] == "1", "text": float(conf["TEXT"]), "motion": conf["MOTION"] != "0"}
