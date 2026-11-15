"""The settings a Firox policy can set (firox-policy).

Each one is a Policy: a key (as written in the policy file), a title and an
explanation for the editor, the kind of value, and how a computer applies
it. Both the editor and the agent that applies policies read this list, so
adding a setting means adding one entry here.

    kind "bool"     True/False             (On / Off in the editor)
    kind "minutes"  a whole number in [low, high]

apply(value) runs as root on the managed computer and returns a short
description of what it did; it raises PolicyError if it couldn't. A setting
missing from the policy ("Not configured") is left alone, and nothing is
locked for it.

Settings that people's own tools must respect (the screen-lock time, Firox
Stream) are also written to MANAGED_DIR, which those tools read:
    power.conf      KEY=VALUE lines that win over Settings (firox-power)
    managed.json    what's in force, for Firox Stream, Security Center and
                    `firox-policy status` (no secret values)
"""

import os
import subprocess

STATE_DIR = "/var/lib/firox/policy"
MANAGED_DIR = STATE_DIR
MANAGED_JSON = os.path.join(MANAGED_DIR, "managed.json")
MANAGED_POWER = os.path.join(MANAGED_DIR, "power.conf")

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


def write_power(key, value):
    """A KEY=VALUE that firox-power reads over Settings (None removes it)."""
    os.makedirs(MANAGED_DIR, exist_ok=True)
    lines = []
    try:
        with open(MANAGED_POWER) as fh:
            lines = [l for l in fh.read().splitlines() if l and not l.startswith(f"{key}=")]
    except OSError:
        pass
    if value is not None:
        lines.append(f"{key}={value}")
    tmp = MANAGED_POWER + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("".join(l + "\n" for l in lines))
    os.chmod(tmp, 0o644)
    os.replace(tmp, MANAGED_POWER)


def restart_idle_timers():
    """Apply power settings in every graphical session now (firox-power
    start, as each user), rather than at their next login."""
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
                        f"WAYLAND_DISPLAY={sockets[0]}", "firox-power", "start"],
                       capture_output=True, timeout=30)


class Policy:
    def __init__(self, key, title, description, kind, apply, on_label="On", off_label="Off",
                 low=None, high=None, unit=None, release=None):
        self.key, self.title, self.description, self.kind = key, title, description, kind
        self._apply, self._release = apply, release
        self.on_label, self.off_label = on_label, off_label
        self.low, self.high, self.unit = low, high, unit

    def check(self, value):
        """The value, checked (PolicyError if it's not one this setting takes)."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise PolicyError(f"{self.key} must be true or false")
        elif self.kind == "minutes":
            if not isinstance(value, int) or isinstance(value, bool) or not self.low <= value <= self.high:
                raise PolicyError(f"{self.key} must be a whole number from {self.low} to {self.high}")
        return value

    def describe(self, value):
        if self.kind == "bool":
            return self.on_label if value else self.off_label
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


POLICIES = [
    Policy("updates.automatic", "Automatic updates",
           "Download and install Firox Linux updates by itself. Off is for computers you update by hand.",
           "bool", apply_updates),
    Policy("firewall.enabled", "Firewall",
           "Keep the firewall on, so only the services Firox opens can be reached from the network.",
           "bool", apply_firewall),
    Policy("ssh.enabled", "Remote login (SSH)",
           "Let people log in to this computer over the network with SSH.",
           "bool", apply_ssh, on_label="Allowed", off_label="Not allowed"),
    Policy("screen.lock_minutes", "Lock the screen when idle",
           "Lock the screen after this many minutes without use. People can't make it longer or turn it "
           "off in Settings.",
           "minutes", apply_lock, low=1, high=240, unit="minutes", release=release_lock),
    Policy("stream.allowed", "Firox Stream hosting",
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
