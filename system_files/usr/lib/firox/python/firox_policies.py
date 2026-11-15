"""The settings a Firox policy can set (firox-policy).

Each one is a Policy: a key (as written in the policy file), a title and an
explanation for the editor, the kind of value, and how a computer applies
it. Both the editor and the agent that applies policies read this list, so
adding a setting means adding one entry here.

    kind "bool"     True/False             (On / Off in the editor)
    kind "minutes"  a whole number in [low, high]
    kind "number"   the same, counting something else (unit says what)
    kind "choice"   one of choices [(value, label)]
    kind "timezone" a time zone name, e.g. America/Chicago
    kind "url"      an http:// or https:// address
    kind "image"    an https:// address of a picture, or a path on the computers

category is where the editor's tree shows it ("Applications/Firefox").

apply(value) runs as root on the managed computer and returns a short
description of what it did; it raises PolicyError if it couldn't. A setting
missing from the policy ("Not configured") is left alone, and nothing is
locked for it.

Settings that people's own tools must respect (the screen-lock time, Firox
Stream) are also written to MANAGED_DIR, which those tools read:
    power.conf      KEY=VALUE lines that win over Settings (firox-power)
    desktop.conf    the same for the desktop (firox_wallpaper: Wallpaper=)
    wallpaper.*     a wallpaper the policy names by address, downloaded
    managed.json    what's in force, for Firox Stream, Security Center and
                    `firox-policy status` (no secret values)
"""

import os
import subprocess

STATE_DIR = "/var/lib/firox/policy"
MANAGED_DIR = STATE_DIR
MANAGED_JSON = os.path.join(MANAGED_DIR, "managed.json")
MANAGED_POWER = os.path.join(MANAGED_DIR, "power.conf")
MANAGED_DESKTOP = os.path.join(MANAGED_DIR, "desktop.conf")
FIREFOX_POLICIES = "/etc/firefox/policies/policies.json"
# The image's own copy (bootc keeps /etc's defaults in /usr/etc); when
# there isn't one, the first version seen is kept here
FIREFOX_DEFAULT = "/usr/etc/firefox/policies/policies.json"
FIREFOX_SAVED = os.path.join(STATE_DIR, "firefox-policies.default.json")
FIREFOX_MANAGED = os.path.join(STATE_DIR, "firefox-managed.json")    # each Firefox setting's part
PWQUALITY = "/etc/security/pwquality.conf.d/50-firox-policy.conf"
USB_BLOCK = "/etc/modprobe.d/firox-policy-usb-storage.conf"
ZONEINFO = "/usr/share/zoneinfo"
MAX_WALLPAPER = 40 << 20

# The same timers Security Center checks (whichever this image has)
UPDATE_TIMERS = ("uupd.timer", "rpm-ostreed-automatic.timer", "bootc-fetch-apply-updates.timer")


class PolicyError(Exception):
    pass


def run(*cmd, check=True):
    p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise PolicyError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                          else f"{' '.join(cmd)} failed")
    return p


def unit_exists(unit):
    return run("systemctl", "cat", unit, check=False).returncode == 0


def set_unit(unit, on):
    run("systemctl", "enable" if on else "disable", "--now", unit)
    return f"{unit} {'on' if on else 'off'}"


def write_kv(path, key, value):
    """Set KEY=VALUE in a managed file people's tools read (None removes it)."""
    os.makedirs(MANAGED_DIR, exist_ok=True)
    lines = []
    try:
        with open(path) as fh:
            lines = [l for l in fh.read().splitlines() if l and not l.startswith(f"{key}=")]
    except OSError:
        pass
    if value is not None:
        lines.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("".join(l + "\n" for l in lines))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_power(key, value):
    """A KEY=VALUE that firox-power reads over Settings (None removes it)."""
    write_kv(MANAGED_POWER, key, value)


def restart_idle_timers():
    """Apply power settings in every graphical session now (firox-power
    start, as each user), rather than at their next login."""
    run_in_sessions("firox-power", "start")


def run_in_sessions(*cmd):
    """Run a command as each logged-in user, in their desktop session."""
    try:
        out = run("loginctl", "list-sessions", "--no-legend", check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        uid, user = parts[1], parts[2]
        runtime = f"/run/user/{uid}"
        try:
            sockets = sorted(n for n in os.listdir(runtime) if n.startswith("wayland-") and not n.endswith(".lock"))
        except OSError:
            continue
        if not sockets:
            continue
        subprocess.run(["runuser", "-u", user, "--", "env", f"XDG_RUNTIME_DIR={runtime}",
                        f"WAYLAND_DISPLAY={sockets[0]}",
                        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus"] + list(cmd),
                       capture_output=True, timeout=60)


class Policy:
    def __init__(self, key, category, title, description, kind, apply, on_label="On", off_label="Off",
                 low=None, high=None, unit=None, release=None, placeholder="", choices=()):
        self.key, self.category, self.title, self.description, self.kind = key, category, title, description, kind
        self.placeholder = placeholder
        self.choices = list(choices)
        self._apply, self._release = apply, release
        self.on_label, self.off_label = on_label, off_label
        self.low, self.high, self.unit = low, high, unit

    def check(self, value):
        """The value, checked (PolicyError if it's not one this setting takes)."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise PolicyError(f"{self.key} must be true or false")
        elif self.kind in ("minutes", "number"):
            if not isinstance(value, int) or isinstance(value, bool) or not self.low <= value <= self.high:
                raise PolicyError(f"{self.key} must be a whole number from {self.low} to {self.high}")
        elif self.kind == "choice":
            if value not in [v for v, _l in self.choices]:
                raise PolicyError(f"{self.key} must be one of " + ", ".join(v for v, _l in self.choices))
        elif self.kind == "timezone":
            import re
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*", value) \
                    or not os.path.isfile(os.path.join(ZONEINFO, value)):
                raise PolicyError(f"{self.key} must be a time zone such as America/Chicago")
        elif self.kind == "url":
            if not isinstance(value, str) or not value.startswith(("https://", "http://")) or len(value) > 2000 \
                    or any(c.isspace() for c in value):
                raise PolicyError(f"{self.key} must be an http:// or https:// address")
        elif self.kind == "image":
            if not isinstance(value, str) or len(value) > 2000 or not (
                    value.startswith("https://") or value.startswith("/")) or "\n" in value:
                raise PolicyError(f"{self.key} must be an https:// address or a path starting with /")
        return value

    def describe(self, value):
        if self.kind == "bool":
            return self.on_label if value else self.off_label
        if self.kind in ("url", "image", "timezone"):
            return str(value)
        if self.kind == "choice":
            return dict(self.choices).get(value, str(value))
        if value == 0 and self.low == 0 and self.kind == "minutes":
            return "Never"
        return f"{value} {self.unit or ''}".strip()

    def apply(self, value):
        return self._apply(self.check(value))

    def release(self):
        """The policy no longer sets this: drop any lock (the setting itself
        stays as it is)."""
        if self._release:
            self._release()


# ----------------------------------------------------------------- the list --

def apply_updates(on):
    timers = [t for t in UPDATE_TIMERS if unit_exists(t)]
    if not timers:
        raise PolicyError("no update timer on this computer")
    # One timer does the updating: the first this image has
    return set_unit(timers[0], on)


def apply_firewall(on):
    return set_unit("firewalld.service", on)


def apply_ssh(on):
    return set_unit("sshd.service", on)


def apply_lock(minutes):
    write_power("LOCK", minutes)
    restart_idle_timers()
    return f"locks after {minutes} min idle"


def release_lock():
    write_power("LOCK", None)
    restart_idle_timers()


def apply_stream(allowed):
    # Read by firox-stream from managed.json; stop running hosts now
    if not allowed:
        run("pkill", "-f", "firox-stream daemon", check=False)
    return "allowed" if allowed else "not allowed (running hosts stopped)"


def apply_wallpaper(value):
    """An https:// picture is downloaded (checked to be an image); a path
    must already be on the computer."""
    if value.startswith("https://"):
        import urllib.request
        ext = os.path.splitext(value.split("?")[0])[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif", ".bmp"):
            ext = ".img"
        req = urllib.request.Request(value, headers={"User-Agent": "firox-policy"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read(MAX_WALLPAPER + 1)
        except OSError as e:
            raise PolicyError(f"couldn't download the wallpaper: {getattr(e, 'reason', e)}") from None
        if len(data) > MAX_WALLPAPER:
            raise PolicyError("the wallpaper is too big (40 MB at most)")
        os.makedirs(MANAGED_DIR, exist_ok=True)
        path = os.path.join(MANAGED_DIR, "wallpaper" + ext)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            GdkPixbuf.Pixbuf.new_from_file(tmp)
        except Exception:                   # noqa: BLE001 - not a picture GTK can show
            os.unlink(tmp)
            raise PolicyError("the wallpaper address isn't a picture") from None
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        for old in os.listdir(MANAGED_DIR):     # an earlier one with another extension
            if old.startswith("wallpaper.") and os.path.join(MANAGED_DIR, old) != path and not old.endswith(".tmp"):
                os.unlink(os.path.join(MANAGED_DIR, old))
    else:
        path = value
        if not os.path.isfile(path):
            raise PolicyError(f"{path} isn't on this computer")
    write_kv(MANAGED_DESKTOP, "Wallpaper", path)
    return f"wallpaper {path}"


def release_wallpaper():
    write_kv(MANAGED_DESKTOP, "Wallpaper", None)


def firefox_default():
    """The Firefox policies this image ships (without any policy's changes)."""
    import json
    for path in (FIREFOX_DEFAULT, FIREFOX_SAVED):
        try:
            with open(path) as fh:
                return json.load(fh), path
        except (OSError, ValueError):
            pass
    # First time without /usr/etc: keep the shipped file as it is now
    import shutil
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        shutil.copyfile(FIREFOX_POLICIES, FIREFOX_SAVED)
        with open(FIREFOX_SAVED) as fh:
            return json.load(fh), FIREFOX_SAVED
    except (OSError, ValueError):
        return {"policies": {}}, None


def firefox_part(key, part):
    """Set (or with None, drop) one policy setting's part of Firefox's
    enterprise policies, then rebuild the file: the image's policies plus
    every part in force. With no parts left it's the image's own file again,
    byte for byte, so updates to it keep arriving. Firefox reads it when it
    starts."""
    import json
    import shutil
    try:
        with open(FIREFOX_MANAGED) as fh:
            parts = json.load(fh)
    except (OSError, ValueError):
        parts = {}
    if part is None:
        parts.pop(key, None)
    else:
        parts[key] = part
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(FIREFOX_MANAGED, "w") as fh:
        json.dump(parts, fh, indent=1)
    base, src = firefox_default()
    os.makedirs(os.path.dirname(FIREFOX_POLICIES), exist_ok=True)
    if not parts:
        if src:
            shutil.copyfile(src, FIREFOX_POLICIES)
            os.chmod(FIREFOX_POLICIES, 0o644)
        return
    policies = json.loads(json.dumps(base.get("policies") or {}))     # a copy
    for k in sorted(parts):
        for name, value in parts[k].items():
            if isinstance(value, dict) and isinstance(policies.get(name), dict):
                policies[name] = dict(policies[name], **value)        # e.g. ExtensionSettings
            else:
                policies[name] = value
    tmp = FIREFOX_POLICIES + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(dict(base, policies=policies), fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, FIREFOX_POLICIES)


def firefox_policy(key, title, description, make, on_label="Allowed", off_label="Not allowed"):
    """A Firefox setting: make(value) is its part of Firefox's policies."""
    def apply(value):
        firefox_part(key, make(value))
        return f"Firefox: {title.lower()} {on_label.lower() if value else off_label.lower()} (at its next start)"
    return Policy(key, "Applications/Firefox", title, description, "bool", apply,
                  on_label=on_label, off_label=off_label, release=lambda: firefox_part(key, None))


def apply_homepage(url):
    firefox_part("firefox.homepage", {"Homepage": {"URL": url, "Locked": True, "StartPage": "homepage"}})
    return f"Firefox home page {url} (at Firefox's next start)"


def release_homepage():
    firefox_part("firefox.homepage", None)


# ---- more settings

def power_setting(*keys):
    """Minutes (0 = never) for firox-power's keys, over Settings."""
    def apply(minutes):
        for k in keys:
            write_power(k, minutes)
        restart_idle_timers()
        return ", ".join(keys) + f" = {minutes}"

    def release():
        for k in keys:
            write_power(k, None)
        restart_idle_timers()
    return apply, release


def apply_lock_on_wake(on):
    write_power("LOCK_ON_WAKE", 1 if on else 0)
    restart_idle_timers()
    return "locks when waking from sleep" if on else "doesn't lock on waking"


def release_lock_on_wake():
    write_power("LOCK_ON_WAKE", None)
    restart_idle_timers()


def apply_timezone(zone):
    run("timedatectl", "set-timezone", zone)
    return f"time zone {zone}"


def apply_ntp(on):
    run("timedatectl", "set-ntp", "true" if on else "false")
    return "network time on" if on else "network time off"


def apply_password_length(n):
    os.makedirs(os.path.dirname(PWQUALITY), exist_ok=True)
    with open(PWQUALITY, "w") as fh:
        fh.write(f"# Set by a Firox policy (firox-policy)\nminlen = {n}\n")
    os.chmod(PWQUALITY, 0o644)
    return f"new passwords need {n} characters"


def release_password_length():
    try:
        os.unlink(PWQUALITY)
    except OSError:
        pass


def apply_usb_storage(allowed):
    if allowed:
        release_usb_storage()
        return "USB drives allowed"
    os.makedirs(os.path.dirname(USB_BLOCK), exist_ok=True)
    with open(USB_BLOCK, "w") as fh:
        fh.write("# Set by a Firox policy (firox-policy): no USB drives\n"
                 "install usb_storage /bin/false\ninstall uas /bin/false\n")
    os.chmod(USB_BLOCK, 0o644)
    # Drives already plugged in keep working until they're unplugged
    run("modprobe", "-r", "uas", "usb_storage", check=False)
    return "USB drives blocked (from the next one plugged in)"


def release_usb_storage():
    try:
        os.unlink(USB_BLOCK)
    except OSError:
        pass


def apply_bluetooth(on):
    if not unit_exists("bluetooth.service"):
        raise PolicyError("no Bluetooth service on this computer")
    return set_unit("bluetooth.service", on)


def apply_virus_definitions(on):
    if not unit_exists("clamav-freshclam.service"):
        raise PolicyError("no virus definition service on this computer")
    return set_unit("clamav-freshclam.service", on)


def apply_theme(name):
    write_kv(MANAGED_DESKTOP, "Theme", name)
    run_in_sessions("firox-theme", "set", name)
    return f"the {name} look"


def release_theme():
    write_kv(MANAGED_DESKTOP, "Theme", None)
    # Everyone's own look again
    run_in_sessions("sh", "-c", 'firox-theme set "$(firox-theme)"')


def apply_sync(allowed):
    if not allowed:
        run("pkill", "-f", "firox-sync daemon", check=False)
    return "allowed" if allowed else "not allowed (running syncs stopped)"


SCREENSAVER = power_setting("SCREENSAVER_WAIT")
MONITOR = power_setting("MONITOR_AC", "MONITOR_BATTERY")
SLEEP = power_setting("SLEEP_AC", "SLEEP_BATTERY")


POLICIES = [
    Policy("time.timezone", "System", "Time zone",
           "The computers' time zone, e.g. America/Chicago or Europe/London.",
           "timezone", apply_timezone, placeholder="America/Chicago"),
    Policy("time.ntp", "System", "Network time",
           "Keep the clock right by asking time servers on the internet.", "bool", apply_ntp),
    Policy("updates.automatic", "System", "Automatic updates",
           "Download and install Firox Linux updates by itself. Off is for computers you update by hand.",
           "bool", apply_updates),
    Policy("firewall.enabled", "Security", "Firewall",
           "Keep the firewall on, so only the services Firox opens can be reached from the network.",
           "bool", apply_firewall),
    Policy("ssh.enabled", "Security", "Remote login (SSH)",
           "Let people log in to this computer over the network with SSH.",
           "bool", apply_ssh, on_label="Allowed", off_label="Not allowed"),
    Policy("screen.lock_minutes", "Security", "Lock the screen when idle",
           "Lock the screen after this many minutes without use. People can't make it longer or turn it "
           "off in Settings.",
           "minutes", apply_lock, low=1, high=240, unit="minutes", release=release_lock),
    Policy("screen.lock_on_wake", "Security", "Lock when waking from sleep",
           "Ask for the password when the computer wakes up.", "bool", apply_lock_on_wake,
           release=release_lock_on_wake),
    Policy("password.min_length", "Security", "Minimum password length",
           "New passwords must be at least this long (when people change their password).",
           "number", apply_password_length, low=6, high=64, unit="characters", release=release_password_length),
    Policy("virus.definitions", "Security", "Virus definition updates",
           "Keep Virus Scan's virus definitions up to date (downloaded every two hours).",
           "bool", apply_virus_definitions),
    Policy("power.screensaver_minutes", "Power", "Screen saver",
           "Start the screen saver after this many idle minutes (0 = never). People can't change it.",
           "minutes", SCREENSAVER[0], low=0, high=240, unit="minutes", release=SCREENSAVER[1]),
    Policy("power.monitor_off_minutes", "Power", "Turn off the screen",
           "Turn the screen off after this many idle minutes (0 = never), plugged in or on battery.",
           "minutes", MONITOR[0], low=0, high=480, unit="minutes", release=MONITOR[1]),
    Policy("power.sleep_minutes", "Power", "Sleep",
           "Put the computer to sleep after this many idle minutes (0 = never), plugged in or on battery.",
           "minutes", SLEEP[0], low=0, high=1440, unit="minutes", release=SLEEP[1]),
    Policy("devices.usb_storage", "Devices", "USB drives",
           "Whether USB drives (memory sticks, external disks) can be used. Blocked takes effect from the "
           "next drive plugged in; keyboards, mice and other USB devices aren't affected.",
           "bool", apply_usb_storage, on_label="Allowed", off_label="Blocked", release=release_usb_storage),
    Policy("devices.bluetooth", "Devices", "Bluetooth",
           "Turn Bluetooth on or off (headphones, mice, phones).", "bool", apply_bluetooth),
    Policy("desktop.theme", "Desktop", "Look",
           "The desktop's look for everyone. People can't switch it in Settings.",
           "choice", apply_theme, release=release_theme,
           choices=[("classic", "Classic"), ("modern", "Modern"), ("glass", "Glass")]),
    Policy("desktop.wallpaper", "Desktop", "Wallpaper",
           "The desktop and lock screen picture. People can't change it. An https:// address is downloaded "
           "by each computer; a path must already be on them (e.g. /usr/share/backgrounds/...).",
           "image", apply_wallpaper, release=release_wallpaper,
           placeholder="https://example.com/wallpaper.jpg"),
    Policy("firefox.homepage", "Applications/Firefox", "Home page",
           "The page Firefox opens at start and with the Home button. People can't change it. Firefox "
           "picks it up the next time it starts.",
           "url", apply_homepage, release=release_homepage, placeholder="https://intranet.example.com"),
    firefox_policy("firefox.private_browsing", "Private browsing",
                   "Whether Firefox's private windows can be opened.",
                   lambda v: {"DisablePrivateBrowsing": not v}),
    firefox_policy("firefox.about_config", "about:config",
                   "Whether Firefox's advanced settings page (about:config) can be opened.",
                   lambda v: {"BlockAboutConfig": not v}),
    firefox_policy("firefox.password_manager", "Saving passwords",
                   "Whether Firefox offers to save passwords.",
                   lambda v: {"PasswordManagerEnabled": v, "OfferToSaveLogins": v}),
    firefox_policy("firefox.developer_tools", "Developer tools",
                   "Whether Firefox's developer tools can be opened.",
                   lambda v: {"DisableDeveloperTools": not v}),
    firefox_policy("firefox.addons", "Installing add-ons",
                   "Whether people can install Firefox add-ons. Firox's own (web apps) stays either way.",
                   lambda v: {"ExtensionSettings": {"*": {"installation_mode": "allowed"} if v else {
                       "installation_mode": "blocked",
                       "blocked_install_message": "Your organization's policy doesn't allow installing add-ons."}}}),
    firefox_policy("firefox.dns_over_https", "DNS over HTTPS",
                   "Whether Firefox looks up web addresses over an encrypted connection. Locked either way.",
                   lambda v: {"DNSOverHTTPS": {"Enabled": v, "Locked": True}}, on_label="On", off_label="Off"),
    Policy("sync.allowed", "Applications/Firox Sync", "Syncing folders",
           "Whether Firox Sync can keep folders the same between computers. Not allowed turns it off and "
           "keeps it off.", "bool", apply_sync, on_label="Allowed", off_label="Not allowed"),
    Policy("stream.allowed", "Applications/Firox Stream", "Hosting",
           "Whether other computers may stream this one with Firox Stream. Not allowed turns it off and "
           "keeps it off; allowed leaves it to each person.",
           "bool", apply_stream, on_label="Allowed", off_label="Not allowed"),
]

BY_KEY = {p.key: p for p in POLICIES}


def managed():
    """What a policy has in force on this computer ({} when unmanaged)."""
    import json
    try:
        with open(MANAGED_JSON) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
