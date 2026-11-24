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
    kind "notice"   {"title": ..., "text": ...}
    kind "records"  a list of {field: value}, the fields described by fields=
                    [Field(...)]; passwords never reach managed.json
    kind "form"     one {field: value} with those fields (joining a domain)
    kind "addresses" a list of IP addresses
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
BANNER_JSON = os.path.join(MANAGED_DIR, "banner.json")     # the login screen (firox-welcome)
BANNER_TXT = os.path.join(MANAGED_DIR, "banner.txt")       # SSH
SSHD_BANNER = "/etc/ssh/sshd_config.d/10-firox-policy-banner.conf"
NM_CONNECTIONS = "/etc/NetworkManager/system-connections"
NM_PREFIX = "firox-policy-"
NM_DNS = "/etc/NetworkManager/conf.d/50-firox-policy-dns.conf"
PRINTERS_STATE = os.path.join(STATE_DIR, "printers.json")
DOMAIN_STATE = os.path.join(STATE_DIR, "domain.json")
SSSD_DOMAIN = "/etc/sssd/conf.d/50-firox-policy-domain.conf"
SSSD_HOMES = "/etc/sssd/conf.d/60-firox-policy-homes.conf"
NFS_STATE = os.path.join(STATE_DIR, "nfs-homes.json")
SYSTEMD_DIR = "/etc/systemd/system"
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


class Field:
    """One field of a "records" setting (a Wi-Fi network, a printer...)."""

    def __init__(self, key, label, kind="text", required=True, choices=(), show_if=None, placeholder="",
                 check=None):
        self.key, self.label, self.kind, self.required = key, label, kind, required
        self.choices = list(choices)        # kind "choice": [(value, label)]
        self.show_if = show_if              # (other field, [values]) it's only used for
        self.placeholder = placeholder
        self.check = check                  # check(value, record) -> error text or None

    def used(self, record):
        return self.show_if is None or record.get(self.show_if[0]) in self.show_if[1]


class Policy:
    def __init__(self, key, category, title, description, kind, apply, on_label="On", off_label="Off",
                 low=None, high=None, unit=None, release=None, placeholder="", choices=(), fields=(),
                 noun=("item", "items")):
        self.key, self.category, self.title, self.description, self.kind = key, category, title, description, kind
        self.placeholder = placeholder
        self.choices = list(choices)
        self.fields = list(fields)
        self.noun = noun
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
        elif self.kind == "notice":
            if not isinstance(value, dict) or not str(value.get("text", "")).strip() or \
                    len(str(value.get("title", ""))) > 200 or len(str(value.get("text", ""))) > 8000:
                raise PolicyError(f"{self.key} needs a message (and at most a 200-character title)")
        elif self.kind == "addresses":
            import ipaddress
            if not isinstance(value, list) or not value or len(value) > 8:
                raise PolicyError(f"{self.key} must list 1 to 8 IP addresses")
            for a in value:
                try:
                    ipaddress.ip_address(str(a))
                except ValueError:
                    raise PolicyError(f"{a} isn't an IP address") from None
        elif self.kind == "records":
            if not isinstance(value, list) or len(value) > 100:
                raise PolicyError(f"{self.key} must be a list (of at most 100)")
            for rec in value:
                err = self.record_error(rec)
                if err:
                    raise PolicyError(err)
        elif self.kind == "form":
            err = self.record_error(value)
            if err:
                raise PolicyError(err)
        elif self.kind == "url":
            if not isinstance(value, str) or not value.startswith(("https://", "http://")) or len(value) > 2000 \
                    or any(c.isspace() for c in value):
                raise PolicyError(f"{self.key} must be an http:// or https:// address")
        elif self.kind == "image":
            if not isinstance(value, str) or len(value) > 2000 or not (
                    value.startswith("https://") or value.startswith("/")) or "\n" in value:
                raise PolicyError(f"{self.key} must be an https:// address or a path starting with /")
        return value

    def record_error(self, rec):
        """What's wrong with one record, or None."""
        if not isinstance(rec, dict):
            return "each entry must be a set of fields"
        for f in self.fields:
            v = rec.get(f.key)
            if not f.used(rec):
                continue
            if f.kind == "bool":
                if v is not None and not isinstance(v, bool):
                    return f"{f.label} must be true or false"
                continue
            if v in (None, ""):
                if f.required:
                    return f"{f.label} is missing"
                continue
            if not isinstance(v, str) or "\n" in v or len(v) > 500:
                return f"{f.label} isn't valid"
            if f.kind == "choice" and v not in [c for c, _l in f.choices]:
                return f"{f.label} must be one of " + ", ".join(c for c, _l in f.choices)
            if f.check:
                err = f.check(v, rec)
                if err:
                    return err
        return None

    def secret(self, key):
        return any(f.key == key and f.kind == "password" for f in self.fields)

    def has_secrets(self, value):
        """Whether this value holds a password (the editor warns: encrypt the policy)."""
        recs = value if self.kind == "records" else [value] if self.kind == "form" else []
        return any(isinstance(r, dict) and any(self.secret(k) and v for k, v in r.items()) for r in recs)

    def public(self, value):
        """The value without secrets, for managed.json and status."""
        if self.kind == "records":
            return [{k: ("••••••" if self.secret(k) and v else v) for k, v in rec.items()} for rec in value]
        if self.kind == "form":
            return {k: ("••••••" if self.secret(k) and v else v) for k, v in value.items()}
        return value

    def describe(self, value):
        if self.kind == "records":
            n = len(value)
            return f"{n} {self.noun[0] if n == 1 else self.noun[1]}" if n else "None (removes the policy's)"
        if self.kind == "notice":
            return value.get("title") or value.get("text", "").strip().splitlines()[0][:60]
        if self.kind == "form":
            return " \u2014 ".join(str(value.get(f.key)) for f in self.fields[:2]
                                    if value.get(f.key) not in (None, "") and f.kind != "password")
        if self.kind == "addresses":
            return ", ".join(value)
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


def apply_rune(allowed):
    # Read by firox-rune from managed.json; put him away now
    if not allowed:
        run("pkill", "-f", "firox-rune", check=False)
    return "allowed" if allowed else "not allowed (Rune put away)"


def apply_rune_chat(allowed):
    # Read by firox-rune when someone asks him something
    return "allowed" if allowed else "not allowed"


def apply_powerwash(allowed):
    # Read by firox-powerwash and /usr/libexec/firox/reset from managed.json
    return "allowed" if allowed else "not allowed"


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


def write_file(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def remove(*paths):
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


# Login banner: the login screen shows it (and waits for OK) before anyone
# logs in; SSH shows it before the password

def apply_banner(value):
    import json
    title, text = str(value.get("title", "")).strip(), str(value["text"]).strip()
    write_file(BANNER_JSON, json.dumps({"title": title, "text": text}))
    write_file(BANNER_TXT, (title + "\n\n" if title else "") + text + "\n")
    write_file(SSHD_BANNER, f"# Set by a Firox policy (firox-policy)\nBanner {BANNER_TXT}\n")
    run("systemctl", "try-reload-or-restart", "sshd.service", check=False)
    return "login banner set (login screen and SSH)"


def release_banner():
    remove(BANNER_JSON, BANNER_TXT, SSHD_BANNER)
    run("systemctl", "try-reload-or-restart", "sshd.service", check=False)


# Wi-Fi networks: NetworkManager connections for everyone

def nm_escape(text):
    return text.replace("\\", "\\\\")


def wifi_file(rec):
    import hashlib
    import re
    import uuid
    ssid = rec["ssid"]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ssid)[:40] + "-" + hashlib.sha256(ssid.encode()).hexdigest()[:6]
    path = os.path.join(NM_CONNECTIONS, f"{NM_PREFIX}{slug}.nmconnection")
    sec = rec.get("security") or "wpa-psk"
    lines = ["# Set by a Firox policy (firox-policy)", "[connection]", f"id={nm_escape(ssid)}",
             f"uuid={uuid.uuid5(uuid.NAMESPACE_URL, 'firox-policy-wifi:' + ssid)}", "type=wifi",
             "autoconnect=true", "", "[wifi]", "mode=infrastructure", f"ssid={nm_escape(ssid)}",
             f"hidden={'true' if rec.get('hidden') else 'false'}", ""]
    if sec in ("wpa-psk", "sae"):
        lines += ["[wifi-security]", f"key-mgmt={sec}", f"psk={nm_escape(rec['password'])}", ""]
    elif sec == "wpa-eap":
        lines += ["[wifi-security]", "key-mgmt=wpa-eap", "", "[802-1x]", "eap=peap;",
                  f"identity={nm_escape(rec['identity'])}", f"password={nm_escape(rec['password'])}",
                  "phase2-auth=mschapv2"]
        if rec.get("domain"):
            lines += [f"domain-suffix-match={nm_escape(rec['domain'])}", "system-ca-certs=true"]
        lines.append("")
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return path, "\n".join(lines)


def apply_wifi(networks):
    keep = set()
    for rec in networks:
        path, text = wifi_file(rec)
        write_file(path, text, 0o600)       # holds the password: root only, as NetworkManager wants
        keep.add(path)
    removed = remove_wifi(keep)
    run("nmcli", "connection", "reload", check=False)
    out = f"{len(networks)} Wi-Fi network{'s' if len(networks) != 1 else ''}"
    return out + (f" ({removed} removed)" if removed else "")


def remove_wifi(keep=()):
    n = 0
    try:
        names = os.listdir(NM_CONNECTIONS)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(NM_CONNECTIONS, name)
        if name.startswith(NM_PREFIX) and name.endswith(".nmconnection") and path not in keep:
            remove(path)
            n += 1
    return n


def release_wifi():
    remove_wifi()
    run("nmcli", "connection", "reload", check=False)


def check_ssid(v, _rec):
    return None if 1 <= len(v.encode()) <= 32 else "a network name is 1 to 32 characters"


def check_wifi_password(v, rec):
    if rec.get("security") in ("wpa-psk", "sae") and not 8 <= len(v) <= 63:
        return f"the password for {rec.get('ssid')} must be 8 to 63 characters"
    return None


WIFI_FIELDS = [
    Field("ssid", "Network name", check=check_ssid, placeholder="Office Wi-Fi"),
    Field("security", "Security", "choice", choices=[
        ("wpa-psk", "WPA/WPA2 Personal"), ("sae", "WPA3 Personal"), ("wpa-eap", "WPA2 Enterprise (PEAP)"),
        ("none", "Open (no password)")]),
    Field("identity", "Username", show_if=("security", ["wpa-eap"])),
    Field("password", "Password", "password", show_if=("security", ["wpa-psk", "sae", "wpa-eap"]),
          check=check_wifi_password),
    Field("domain", "Server domain", required=False,
          show_if=("security", ["wpa-eap"]), placeholder="radius.example.com (its certificate is checked)"),
    Field("hidden", "Hidden network (doesn't broadcast its name)", "bool", required=False),
]


# Printers: CUPS queues, driverless (IPP Everywhere)

def check_printer_name(v, _rec):
    import re
    return None if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v) else \
        "a printer name uses letters, digits, - and _ (no spaces)"


def check_printer_uri(v, _rec):
    ok = v.split(":", 1)[0] in ("ipp", "ipps", "http", "https", "lpd", "socket", "smb", "dnssd")
    return None if ok and "://" in v else "the address must look like ipp://printer.example.com/ipp/print"


def apply_printers(printers):
    import json
    try:
        with open(PRINTERS_STATE) as fh:
            before = set(json.load(fh))
    except (OSError, ValueError):
        before = set()
    done, failed = [], []
    for rec in printers:
        cmd = ["lpadmin", "-p", rec["name"], "-E", "-v", rec["uri"], "-m", "everywhere"]
        if rec.get("description"):
            cmd += ["-D", rec["description"]]
        if rec.get("location"):
            cmd += ["-L", rec["location"]]
        p = run(*cmd, check=False)
        (done if p.returncode == 0 else failed).append(rec["name"])
        if p.returncode == 0 and rec.get("default"):
            run("lpadmin", "-d", rec["name"], check=False)
    for name in before - {r["name"] for r in printers}:
        run("lpadmin", "-x", name, check=False)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PRINTERS_STATE, "w") as fh:
        json.dump([r["name"] for r in printers if r["name"] in done] + [n for n in before if n in failed], fh)
    if failed:
        raise PolicyError(f"couldn't add {', '.join(failed)} (is it switched on and reachable?); "
                          f"added {len(done)}")
    return f"{len(done)} printer{'s' if len(done) != 1 else ''}"


def release_printers():
    apply_printers([])


PRINTER_FIELDS = [
    Field("name", "Name", check=check_printer_name, placeholder="Office-Laser"),
    Field("uri", "Address", check=check_printer_uri, placeholder="ipp://printer.example.com/ipp/print"),
    Field("description", "Description", required=False, placeholder="Second floor laser printer"),
    Field("location", "Location", required=False, placeholder="Room 204"),
    Field("default", "Make it the default printer", "bool", required=False),
]


# DNS servers for every connection

def apply_dns(servers):
    write_file(NM_DNS, "# Set by a Firox policy (firox-policy)\n[global-dns-domain-*]\n"
               f"servers={','.join(servers)}\n")
    run("systemctl", "try-reload-or-restart", "NetworkManager.service", check=False)
    return "DNS servers " + ", ".join(servers)


def release_dns():
    remove(NM_DNS)
    run("systemctl", "try-reload-or-restart", "NetworkManager.service", check=False)


# Firefox bookmarks: a managed folder on the bookmarks toolbar

def check_bookmark_url(v, _rec):
    return None if v.startswith(("https://", "http://")) else "a bookmark address starts with https://"


def apply_bookmarks(bookmarks):
    part = {"ManagedBookmarks": [{"toplevel_name": "From your organization"}] +
            [{"name": b["name"], "url": b["url"]} for b in bookmarks]} if bookmarks else None
    firefox_part("firefox.bookmarks", part)
    return f"{len(bookmarks)} Firefox bookmark{'s' if len(bookmarks) != 1 else ''} (at its next start)"


BOOKMARK_FIELDS = [
    Field("name", "Name", placeholder="Intranet"),
    Field("url", "Address", check=check_bookmark_url, placeholder="https://intranet.example.com"),
]


SCREENSAVER = power_setting("SCREENSAVER_WAIT")
MONITOR = power_setting("MONITOR_AC", "MONITOR_BATTERY")
SLEEP = power_setting("SLEEP_AC", "SLEEP_BATTERY")


# Active Directory: joined with realmd (adcli + sssd), as `realm join` would.
# The join account's password is used once and never kept on the computer
# outside the (root-only) policy; managed.json shows it as dots.

def check_domain(v, _rec):
    import re
    if not re.fullmatch(r"(?=.{1,253}$)([A-Za-z0-9-]{1,63}\.)+[A-Za-z0-9-]{2,63}", v):
        return f"{v} isn't a domain name such as corp.example.com"
    return None


def check_join_user(v, _rec):
    import re
    if not re.fullmatch(r"[^\s\\/:*?\"<>|@][^\\/:*?\"<>|]{0,63}(@[A-Za-z0-9.-]+)?", v):
        return f"{v} isn't an account name"
    return None


def check_ou(v, _rec):
    import re
    if not re.fullmatch(r"(OU|CN)=[^,=]+(,(OU|CN|DC)=[^,=]+)*", v, re.IGNORECASE):
        return "The OU must be written like OU=Workstations,DC=corp,DC=example,DC=com"
    return None


def check_computer_name(v, _rec):
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,14}", v):
        return "A computer name is 1 to 15 letters, digits or dashes (the AD limit)"
    return None


def check_groups(v, _rec):
    groups = [g.strip() for g in v.split(",") if g.strip()]
    if not groups or any(len(g) > 256 or any(c in g for c in "\n:") for g in groups):
        return "List the groups separated by commas"
    return None


DOMAIN_FIELDS = [
    Field("domain", "Domain", placeholder="corp.example.com", check=check_domain),
    Field("user", "Join account", placeholder="an account allowed to join computers", check=check_join_user),
    Field("password", "Its password", kind="password"),
    Field("ou", "Put computers in (OU)", required=False, placeholder="OU=Workstations,DC=corp,DC=example,DC=com",
          check=check_ou),
    Field("computer_name", "Computer name", required=False,
          placeholder="leave empty to use each computer's own name", check=check_computer_name),
    Field("groups", "Who may log in", required=False,
          placeholder="groups, e.g. Domain Users, IT Staff (empty: everyone in the domain)", check=check_groups),
    Field("short_names", "Log in with just the user name (jsmith, not jsmith@corp.example.com)", kind="bool",
          required=False),
    Field("posix", "Use the UID and GID numbers set in Active Directory (uidNumber/gidNumber), e.g. for NFS",
          kind="bool", required=False),
]


def read_json_file(path, default):
    import json
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json_file(path, data):
    import json
    write_file(path, json.dumps(data, indent=1), 0o600)


def joined_domains():
    """The realms this computer is joined to (lower case)."""
    out = run("realm", "list", "--name-only", check=False).stdout
    return [l.strip().lower() for l in out.splitlines() if l.strip()]


def authselect_current():
    """The authselect profile and features now (to put back when leaving)."""
    p = run("authselect", "current", "--raw", check=False)
    return p.stdout.strip() if p.returncode == 0 else ""


def apply_domain(value):
    domain = value["domain"].lower()
    state = read_json_file(DOMAIN_STATE, {})
    done = []
    joined = joined_domains()
    for other in joined:
        if other != domain:
            run("realm", "leave", other, check=False)
            done.append(f"left {other}")
    if domain not in joined:
        if "previous_auth" not in state:
            state["previous_auth"] = authselect_current() or "local"
        cmd = ["realm", "join", "--verbose", f"--user={value['user']}", "--membership-software=adcli",
               "--client-software=sssd"]
        if value.get("ou"):
            cmd.append(f"--computer-ou={value['ou']}")
        if value.get("computer_name"):
            cmd.append(f"--computer-name={value['computer_name']}")
        cmd.append(domain)
        # The password goes in on standard input, never on a command line
        p = subprocess.run(cmd, input=value["password"] + "\n", capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            lines = [l for l in (p.stderr or p.stdout).splitlines() if l.strip()
                     and not l.startswith((" *", "Password for"))]
            raise PolicyError(f"couldn't join {domain}: " + (lines[-1] if lines else "realm join failed"))
        done.append(f"joined {domain}")
    # sssd settings for the domain: how names look, where homes are, which IDs
    short = bool(value.get("short_names"))
    lines = [f"[domain/{domain}]",
             f"use_fully_qualified_names = {'False' if short else 'True'}",
             f"fallback_homedir = /home/{'%u' if short else '%u@%d'}"]
    if value.get("posix"):
        lines.append("ldap_id_mapping = False")
    text = "# Written by firox-policy (accounts.domain); changes here are overwritten.\n" + \
        "\n".join(lines) + "\n"
    if read_text(SSSD_DOMAIN) != text:
        write_file(SSSD_DOMAIN, text, 0o600)
        restart_sssd()
        done.append("sssd settings")
    # Home folders made at the first login
    if "with-mkhomedir" not in authselect_current().split():
        run("authselect", "select", "sssd", "with-mkhomedir", "--force")
    if unit_exists("oddjobd.service"):
        run("systemctl", "enable", "--now", "oddjobd.service", check=False)
    # Who may log in
    groups = [g.strip() for g in (value.get("groups") or "").split(",") if g.strip()]
    if groups != state.get("groups"):
        if groups:
            run("realm", "deny", "--all")
            run("realm", "permit", "--groups", *groups)
        else:
            run("realm", "permit", "--all")
        done.append("logins: " + (", ".join(groups) if groups else "everyone in the domain"))
    state.update(domain=domain, groups=groups)
    write_json_file(DOMAIN_STATE, state)
    return "; ".join(done) if done else f"joined to {domain}"


def read_text(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def restart_sssd():
    if unit_exists("sssd.service"):
        run("systemctl", "restart", "sssd.service", check=False)


def release_domain():
    state = read_json_file(DOMAIN_STATE, {})
    for d in joined_domains():
        run("realm", "leave", d, check=False)
    remove(SSSD_DOMAIN)
    previous = (state.get("previous_auth") or "local").split()
    run("authselect", "select", *previous, "--force", check=False)
    remove(DOMAIN_STATE)


# NFS home folders: the export mounted on demand (systemd automount, no
# autofs), and optionally where domain users' home folders are

def check_host(v, _rec):
    import ipaddress
    import re
    try:
        ipaddress.ip_address(v.strip("[]"))
        return None
    except ValueError:
        pass
    if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9-]{1,63})*", v):
        return f"{v} isn't a server name or IP address"
    return None


def check_export(v, _rec):
    import re
    if not v.startswith("/") or ".." in v.split("/") or not re.fullmatch(r"/[A-Za-z0-9._/@+-]*", v):
        return "The export is a path on the server, e.g. /export/home"
    return None


NFS_PLACES = ("/var/", "/mnt/", "/srv/")
NFS_NOT = ("/var/home", "/var/lib", "/var/log", "/var/cache", "/var/tmp", "/var/roothome", "/var/usrlocal",
           "/var/opt", "/var/mnt", "/var/srv")


def check_mount_at(v, _rec):
    import re
    p = os.path.normpath(v)
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", v) or ".." in v.split("/") or \
            not p.startswith(NFS_PLACES) or any(p == n or p.startswith(n + "/") for n in NFS_NOT):
        return ("Mount it in a folder of its own under /var, /mnt or /srv (the rest of the system is "
                "read-only), e.g. /var/nethome; not over the local home folders")
    return None


def check_options(v, _rec):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_=.,:-]+", v):
        return "Extra options are separated by commas, e.g. noatime,rsize=1048576"
    return None


NFS_FIELDS = [
    Field("server", "Server", placeholder="files.corp.example.com", check=check_host),
    Field("export", "Export", placeholder="/export/home", check=check_export),
    Field("mount_at", "Mount at", required=False, placeholder="/var/nethome", check=check_mount_at),
    Field("version", "NFS version", kind="choice",
          choices=[("4.2", "NFS 4.2"), ("4.1", "NFS 4.1"), ("4", "NFS 4.0"), ("3", "NFS 3")]),
    Field("security", "Security", kind="choice",
          choices=[("sys", "User IDs (sec=sys)"), ("krb5", "Kerberos (sec=krb5, needs the domain)"),
                   ("krb5i", "Kerberos with integrity (krb5i)"), ("krb5p", "Kerberos, encrypted (krb5p)")]),
    Field("options", "Extra mount options", required=False, placeholder="noatime", check=check_options),
    Field("homes", "Domain users' home folders are here (MOUNT/username)", kind="bool", required=False),
]


def nfs_units(where):
    name = run("systemd-escape", "--path", where).stdout.strip()
    return f"{name}.mount", f"{name}.automount"


def apply_nfs_homes(value):
    where = os.path.normpath(value.get("mount_at") or "/var/nethome")
    old = read_json_file(NFS_STATE, {})
    if old.get("where") and old["where"] != where:
        release_nfs_homes(keep_homes=True)
    mount, automount = nfs_units(where)
    server = value["server"]
    if ":" in server and not server.startswith("["):
        server = f"[{server}]"                     # an IPv6 address
    sec = value.get("security") or "sys"
    opts = [f"vers={value.get('version') or '4.2'}", f"sec={sec}", "_netdev", "nosuid", "nodev"]
    opts += [o for o in (value.get("options") or "").split(",") if o]
    source = f"{server}:{value['export']}"
    units = {
        mount: "# Written by firox-policy (storage.nfs_homes)\n[Unit]\n"
               f"Description=Home folders from {source} (firox-policy)\n"
               "Wants=network-online.target\nAfter=network-online.target\n\n"
               f"[Mount]\nWhat={source}\nWhere={where}\nType=nfs\nOptions={','.join(opts)}\n"
               "TimeoutSec=30\n",
        automount: "# Written by firox-policy (storage.nfs_homes)\n[Unit]\n"
                   f"Description=Home folders from {source}, mounted when used (firox-policy)\n\n"
                   f"[Automount]\nWhere={where}\nTimeoutIdleSec=0\n\n"
                   "[Install]\nWantedBy=remote-fs.target\n",
    }
    changed = False
    for name, text in units.items():
        path = os.path.join(SYSTEMD_DIR, name)
        if read_text(path) != text:
            write_file(path, text)
            changed = True
    os.makedirs(where, mode=0o755, exist_ok=True)
    if sec.startswith("krb5"):
        # Kerberos needs the host's keytab (from joining the domain) and gssd
        run("systemctl", "enable", "--now", "rpc-gssd.service", check=False)
    if changed:
        run("systemctl", "daemon-reload")
        run("systemctl", "stop", mount, check=False)
        run("systemctl", "enable", automount)
        run("systemctl", "restart", automount)
    # Domain users' home folders
    homes = bool(value.get("homes"))
    text = ("# Written by firox-policy (storage.nfs_homes)\n[nss]\n"
            f"override_homedir = {where}/%u\n") if homes else None
    if read_text(SSSD_HOMES) != text:
        if text:
            write_file(SSSD_HOMES, text, 0o600)
        else:
            remove(SSSD_HOMES)
        restart_sssd()
    write_json_file(NFS_STATE, {"where": where, "units": [mount, automount]})
    what = f"{source} at {where}" + (" (mounted when used)" if changed else "")
    return what + ("; domain users' home folders are there" if homes else "")


def release_nfs_homes(keep_homes=False):
    state = read_json_file(NFS_STATE, {})
    units = state.get("units") or []
    for unit in reversed(units):
        run("systemctl", "disable", "--now", unit, check=False)
    for unit in units:
        remove(os.path.join(SYSTEMD_DIR, unit))
    if units:
        run("systemctl", "daemon-reload", check=False)
    if not keep_homes and read_text(SSSD_HOMES) is not None:
        remove(SSSD_HOMES)
        restart_sssd()
    remove(NFS_STATE)


POLICIES = [
    Policy("login.banner", "System", "Login banner",
           "A notice everyone must acknowledge on the login screen before logging in (and that SSH shows "
           "before the password), e.g. an acceptable-use statement.",
           "notice", apply_banner, release=release_banner),
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
    Policy("system.powerwash", "Security", "Powerwash",
           "Whether people may Powerwash this computer (delete every account and start again with first-time "
           "setup). Not allowed hides it; the computer stays enrolled either way.",
           "bool", apply_powerwash, on_label="Allowed", off_label="Not allowed"),
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
    Policy("network.wifi", "Network", "Wi-Fi networks",
           "Wi-Fi networks every computer knows and joins by itself. Passwords are kept for the system, "
           "readable only by root. Encrypt policies that hold Wi-Fi passwords.",
           "records", apply_wifi, release=release_wifi, fields=WIFI_FIELDS, noun=("network", "networks")),
    Policy("network.dns", "Network", "DNS servers",
           "The DNS servers every connection uses, instead of the ones the network offers. Separate "
           "them with commas.", "addresses", apply_dns, release=release_dns,
           placeholder="9.9.9.9, 149.112.112.112"),
    Policy("accounts.domain", "Network/Domain", "Active Directory",
           "Join every computer to an Active Directory domain (realmd, with SSSD), so people log in with "
           "their domain accounts: on the login screen, click Other user\u2026 and type the user name. Home "
           "folders are made at the first login. The join account's password is used only to join; "
           "encrypt policies that hold it. Removing this setting leaves the domain.",
           "form", apply_domain, release=release_domain, fields=DOMAIN_FIELDS),
    Policy("storage.nfs_homes", "Network/Domain", "NFS home folders",
           "Mount home folders from an NFS server on every computer, when they're first used (systemd "
           "automount). With \u201cDomain users' home folders are here\u201d, people who log in with a "
           "domain account get MOUNT/username as their home folder; it has to exist on the server and "
           "belong to them. Kerberos security needs Active Directory set too; with user IDs (sec=sys), "
           "use the UID numbers from Active Directory so they match the server's.",
           "form", apply_nfs_homes, release=release_nfs_homes, fields=NFS_FIELDS),
    Policy("devices.printers", "Devices", "Printers",
           "Printers every computer has, added driverless (IPP Everywhere): most network printers "
           "made in the last ten years work.", "records", apply_printers, release=release_printers,
           fields=PRINTER_FIELDS, noun=("printer", "printers")),
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
    Policy("firefox.bookmarks", "Applications/Firefox", "Bookmarks",
           "Bookmarks in a \u201cFrom your organization\u201d folder on Firefox's bookmarks toolbar. People "
           "can't change them.", "records", apply_bookmarks,
           release=lambda: firefox_part("firefox.bookmarks", None), fields=BOOKMARK_FIELDS,
           noun=("bookmark", "bookmarks")),
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
    Policy("rune.allowed", "Applications/Rune", "Rune on the desktop",
           "Whether Rune, the Firox mascot, can sit on the desktop at all. Not allowed puts him away and keeps "
           "him away; allowed leaves it to each person (he can be put away from his menu).",
           "bool", apply_rune, on_label="Allowed", off_label="Not allowed"),
    Policy("rune.chat", "Applications/Rune", "Chatting",
           "Whether Rune can answer questions and do things for people, using the AI service they pick "
           "(Claude, OpenRouter, OpenAI or Gemini) with their own API key. Not allowed keeps him as a mascot "
           "only: nothing is ever sent to any of them.",
           "bool", apply_rune_chat, on_label="Allowed", off_label="Not allowed"),
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
