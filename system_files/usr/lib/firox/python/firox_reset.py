"""The engine behind System Preparation (firox-sysprep) and Powerwash
(firox-powerwash): what to reset, planned now and done at shutdown.

A plan is a few switches:

    mode           "sysprep" or "powerwash"
    oobe           next start shows first-time setup (an account, the
                   computer's name, time zone, keyboard), see firox-oobe
    audit          audit mode, as in Windows: every start logs straight into a
                   temporary administrator account, "audit" (no password, no
                   password asked for admin tasks), to install and set things
                   up; System Preparation opens there. Running it again
                   without audit mode leaves it: the account, its rights and
                   the automatic login go
    generalize     forget what makes this computer itself: its machine ID,
                   SSH host keys, name, logs, saved networks, domain
                   membership (sysprep, for computers about to be cloned)
    remove_users   delete every person's account and files
    reset_network  forget Wi-Fi and other saved connections
    reset_bluetooth forget paired Bluetooth devices
    reset_packages take out packages added on top of the system image
                   (rpm-ostree reset; done straight away, it takes effect
                   at the next start)
    after          "poweroff", "reboot" or "none" (wait for the next
                   shutdown, whenever that is)

Scheduling (as root, through /usr/libexec/firox/reset) writes the plan to
/var/lib/firox/reset/plan.json with this boot's ID and starts
firox-reset.service. That unit does nothing until it's stopped, which
happens at shutdown, after everyone has been logged out (it's ordered
before user.slice and systemd-user-sessions): then the plan is carried out.
A plan from an earlier boot (the computer lost power, say) is never carried
out. Cancelling removes the plan first, so stopping the unit does nothing.

Every step is logged to the journal (firox-reset) and to
/var/lib/firox/reset/last.log, which survives for the next start.
"""

import json
import os
import pwd
import shutil
import subprocess
import time

STATE_DIR = "/var/lib/firox/reset"
PLAN = os.path.join(STATE_DIR, "plan.json")
LAST_LOG = os.path.join(STATE_DIR, "last.log")
OOBE_FLAG = "/var/lib/firox/oobe"           # firox-greeter shows first-time setup while it exists
AUDIT_FLAG = "/var/lib/firox/audit"         # in audit mode
AUDIT_USER = "audit"
GREETD_CONF = "/etc/greetd/config.toml"
GREETD_BACKUP = os.path.join(STATE_DIR, "greetd-config.toml")
AUDIT_POLKIT = "/etc/polkit-1/rules.d/49-firox-audit.rules"
AUDIT_SUDO = "/etc/sudoers.d/firox-audit"
UNIT = "firox-reset.service"
HELPER = "/usr/libexec/firox/reset"
MANAGED = "/var/lib/firox/policy/managed.json"

SWITCHES = ("oobe", "audit", "generalize", "remove_users", "reset_network", "reset_bluetooth", "reset_packages")
AFTER = ("poweroff", "reboot", "none")

POWERWASH = {"mode": "powerwash", "oobe": True, "audit": False, "generalize": False, "remove_users": True,
             "reset_network": True, "reset_bluetooth": True, "reset_packages": True, "after": "reboot"}


class ResetError(Exception):
    pass


# ----------------------------------------------------------------- plans --

def check_plan(plan):
    """A clean copy of plan, or ResetError. Removing everyone's accounts
    without first-time setup would leave nobody able to log in, so that
    turns setup on."""
    if not isinstance(plan, dict):
        raise ResetError("the plan isn't a list of settings")
    mode = plan.get("mode")
    if mode not in ("sysprep", "powerwash"):
        raise ResetError("the plan's mode must be sysprep or powerwash")
    clean = {"mode": mode}
    for k in SWITCHES:
        v = plan.get(k, False)
        if not isinstance(v, bool):
            raise ResetError(f"{k} must be true or false")
        clean[k] = v
    after = plan.get("after", "none")
    if after not in AFTER:
        raise ResetError(f"after must be one of {', '.join(AFTER)}")
    clean["after"] = after
    if clean["audit"]:
        clean["oobe"] = False               # one or the other: audit mode comes first
    elif clean["remove_users"]:
        clean["oobe"] = True
    if mode == "powerwash":
        clean = dict(POWERWASH, after=clean["after"] if clean["after"] != "none" else "reboot")
    if not any(clean[k] for k in SWITCHES) and not in_audit():
        raise ResetError("there's nothing to do: choose first-time setup, generalize or another reset")
    return clean


def powerwash_forbidden():
    """True if a policy (firox-policy) doesn't allow Powerwash."""
    try:
        with open(MANAGED) as fh:
            return (json.load(fh).get("settings") or {}).get("system.powerwash") is False
    except (OSError, ValueError, AttributeError):
        return False


def managed_by():
    """Who manages this computer by policy (its enrollment name), or None."""
    try:
        with open(MANAGED) as fh:
            data = json.load(fh)
        return data.get("organization") or "your organization"
    except (OSError, ValueError, AttributeError):
        return None


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def pending():
    """The plan waiting for this boot's shutdown, or None."""
    try:
        with open(PLAN) as fh:
            plan = json.load(fh)
        if plan.get("boot_id") == boot_id():
            return plan
    except (OSError, ValueError, AttributeError):
        pass
    return None


def in_audit():
    return os.path.exists(AUDIT_FLAG)


def describe(plan):
    """What a plan will do, as short lines for people."""
    out = []
    if in_audit() and not plan.get("audit"):
        out.append("Leave audit mode: the audit account and its files go, and the computer no longer "
                   "logs in by itself")
    if plan.get("remove_users"):
        out.append("Delete every account on this computer and everything in it (documents, pictures, "
                   "settings, apps installed for one person)")
    if plan.get("generalize"):
        out.append("Generalize: forget this computer's machine ID, SSH host keys, name, logs, saved "
                   "networks, Bluetooth pairings and domain membership, so each copy made from it is new")
    else:
        if plan.get("reset_network"):
            out.append("Forget saved Wi-Fi and other network connections")
    if plan.get("reset_bluetooth") and not plan.get("generalize"):
        out.append("Forget paired Bluetooth devices")
    if plan.get("reset_packages"):
        out.append("Take out packages added on top of Firox (the system goes back to the image as installed)")
    if plan.get("oobe"):
        out.append("Show first-time setup at the next start: a new account, the computer's name, "
                   "time zone and keyboard")
    if plan.get("audit"):
        out.append("Start in audit mode: every start logs straight into a temporary administrator "
                   "account (audit, no password) to install and set things up; System Preparation "
                   "opens there. Run it again with first-time setup to finish")
    out.append({"poweroff": "Then shut down", "reboot": "Then restart",
                "none": "All this happens the next time the computer shuts down or restarts"}[plan["after"]])
    return out


# --------------------------------------------------------- as the person --

def schedule(plan):
    """Ask for the administrator password (pkexec) and hand plan to the
    root helper. Returns (ok, message)."""
    try:
        p = subprocess.run(["pkexec", HELPER, "schedule"], input=json.dumps(plan), capture_output=True,
                           text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if p.returncode in (126, 127) and not p.stdout:
        return False, "cancelled"
    return p.returncode == 0, (p.stdout or p.stderr).strip()


def cancel():
    try:
        p = subprocess.run(["pkexec", HELPER, "cancel"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout or p.stderr).strip()


# --------------------------------------------------------------- as root --

def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LAST_LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run(*cmd, timeout=120):
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"  {' '.join(cmd)}: {e}")
        return False
    if p.returncode != 0:
        log(f"  {' '.join(cmd)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.returncode == 0


def save_plan(plan):
    # Readable by everyone (it holds no secrets): the windows show what's waiting
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    tmp = PLAN + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(dict(plan, boot_id=boot_id(), planned=time.time()), fh, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, PLAN)


def drop_plan():
    try:
        os.unlink(PLAN)
    except OSError:
        pass


def layered_packages():
    """Packages added on top of the image (rpm-ostree), or []."""
    try:
        out = subprocess.run(["rpm-ostree", "status", "--json"], capture_output=True, text=True,
                             timeout=60).stdout
        for d in json.loads(out).get("deployments", []):
            if d.get("booted"):
                return (d.get("requested-packages") or []) + (d.get("requested-local-packages") or [])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return []


def login_range():
    lo, hi = 1000, 60000
    try:
        with open("/etc/login.defs") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2 and parts[0] in ("UID_MIN", "UID_MAX"):
                    if parts[0] == "UID_MIN":
                        lo = int(parts[1])
                    else:
                        hi = int(parts[1])
    except (OSError, ValueError):
        pass
    return lo, hi


def people():
    """Local accounts people log in with (not system accounts)."""
    lo, hi = login_range()
    return [e for e in pwd.getpwall() if lo <= e.pw_uid <= hi and e.pw_name != "nobody"]


def empty_dir(path, keep=()):
    """Delete what's in path (not path itself)."""
    try:
        names = os.listdir(path)
    except OSError:
        return 0
    n = 0
    for name in names:
        if name in keep:
            continue
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
            n += 1
        except OSError as e:
            log(f"  couldn't delete {p}: {e}")
    return n


def remove_files(paths):
    n = 0
    for p in paths:
        try:
            os.unlink(p)
            n += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            log(f"  couldn't delete {p}: {e}")
    return n


def remove_users():
    for e in people():
        log(f"deleting the account {e.pw_name} ({e.pw_dir})")
        run("userdel", "--remove", "--force", e.pw_name)
        if os.path.isdir(e.pw_dir) and e.pw_dir.startswith(("/var/home/", "/home/")):
            shutil.rmtree(e.pw_dir, ignore_errors=True)
        remove_files([f"/var/lib/AccountsService/users/{e.pw_name}",
                      f"/var/lib/AccountsService/icons/{e.pw_name}",
                      f"/var/spool/mail/{e.pw_name}"])


def reset_network():
    n = empty_dir("/etc/NetworkManager/system-connections")
    log(f"forgot {n} saved network connection(s)")


def reset_bluetooth():
    n = empty_dir("/var/lib/bluetooth")
    log(f"forgot the Bluetooth pairings ({n} adapter folder(s))")


def generalize():
    log("generalizing")
    with open("/etc/machine-id", "w") as fh:
        fh.write("uninitialized\n")         # systemd makes a new one at the next start
    if os.path.isfile("/var/lib/dbus/machine-id") and not os.path.islink("/var/lib/dbus/machine-id"):
        remove_files(["/var/lib/dbus/machine-id"])
    keys = [os.path.join("/etc/ssh", f) for f in os.listdir("/etc/ssh") if f.startswith("ssh_host_")] \
        if os.path.isdir("/etc/ssh") else []
    log(f"  removed {remove_files(keys)} SSH host key file(s) (new ones are made at the next start)")
    remove_files(["/etc/hostname", "/var/lib/systemd/random-seed", "/var/lib/systemd/credential.secret",
                  "/var/lib/NetworkManager/secret_key", "/var/lib/NetworkManager/seen-bssids",
                  "/var/lib/NetworkManager/timestamps"])
    remove_files([os.path.join("/var/lib/NetworkManager", f) for f in os.listdir("/var/lib/NetworkManager")
                  if f.endswith(".lease")] if os.path.isdir("/var/lib/NetworkManager") else [])
    reset_network()
    reset_bluetooth()                       # (pairings are with this computer's adapter)
    # A computer account in a domain belongs to this computer only; a
    # policy that joins computers joins each copy under its own name
    if shutil.which("realm") and subprocess.run(["realm", "list", "--name-only"], capture_output=True,
                                                 text=True, timeout=30).stdout.strip():
        log("  leaving the domain")
        run("realm", "leave", timeout=60)
    remove_files(["/var/lib/firox/policy/domain.json"])
    log(f"  emptied the logs ({empty_dir('/var/log/journal')} journal folder(s))")
    empty_dir("/var/log/audit")
    for name in ("btmp", "wtmp", "lastlog"):
        try:
            open(os.path.join("/var/log", name), "w").close()
        except OSError:
            pass
    empty_dir("/var/tmp")
    empty_dir("/var/cache/firox")


def enter_audit():
    """Audit mode: the audit account (an administrator with no password,
    needing none for admin tasks), logged in at every start by greetd."""
    log("entering audit mode")
    try:
        pwd.getpwnam(AUDIT_USER)
    except KeyError:
        run("useradd", "--create-home", "--comment", "Audit (System Preparation)", "--groups", "wheel",
            AUDIT_USER)
    run("passwd", "--delete", AUDIT_USER)
    try:
        e = pwd.getpwnam(AUDIT_USER)
        # No screen lock: there's no password to unlock it with
        conf = os.path.join(e.pw_dir, ".config", "firox")
        os.makedirs(conf, exist_ok=True)
        with open(os.path.join(conf, "power.conf"), "w") as fh:
            fh.write("LOCK=0\nLOCK_ON_WAKE=0\n")
        for d, _dirs, files in os.walk(os.path.join(e.pw_dir, ".config")):
            os.chown(d, e.pw_uid, e.pw_gid)
            for f in files:
                os.chown(os.path.join(d, f), e.pw_uid, e.pw_gid)
    except (KeyError, OSError) as err:
        log(f"  {err}")
    os.makedirs(os.path.dirname(AUDIT_POLKIT), exist_ok=True)
    with open(AUDIT_POLKIT, "w") as fh:
        fh.write("// Audit mode (System Preparation): the audit account needs no password for\n"
                 "// admin tasks. Removed when audit mode ends.\n"
                 "polkit.addRule(function(action, subject) {\n"
                 f"    if (subject.user == \"{AUDIT_USER}\") {{ return polkit.Result.YES; }}\n"
                 "});\n")
    os.chmod(AUDIT_POLKIT, 0o644)
    os.makedirs(os.path.dirname(AUDIT_SUDO), exist_ok=True)
    with open(AUDIT_SUDO, "w") as fh:
        fh.write(f"# Audit mode (System Preparation); removed when it ends\n{AUDIT_USER} ALL=(ALL) NOPASSWD: ALL\n")
    os.chmod(AUDIT_SUDO, 0o440)
    # greetd logs it in by itself at every start ([initial_session])
    try:
        with open(GREETD_CONF) as fh:
            conf = fh.read()
    except OSError:
        conf = ""
    if not os.path.exists(GREETD_BACKUP):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(GREETD_BACKUP, "w") as fh:
            fh.write(conf)
    if "[initial_session]" not in conf:
        with open(GREETD_CONF, "w") as fh:
            fh.write(conf.rstrip("\n") + "\n\n# Audit mode (System Preparation): log in by itself. "
                     "Removed when it ends.\n[initial_session]\ncommand = \"firox-session\"\n"
                     f"user = \"{AUDIT_USER}\"\n")
    remove_files([OOBE_FLAG])
    with open(AUDIT_FLAG, "w") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    os.chmod(AUDIT_FLAG, 0o644)


def leave_audit():
    log("leaving audit mode")
    try:
        with open(GREETD_BACKUP) as fh:
            original = fh.read()
        with open(GREETD_CONF, "w") as fh:
            fh.write(original)
        remove_files([GREETD_BACKUP])
    except OSError:
        # No copy: take out what audit mode added
        try:
            with open(GREETD_CONF) as fh:
                conf = fh.read()
            if "[initial_session]" in conf:
                conf = conf.split("\n# Audit mode (System Preparation)")[0].split("[initial_session]")[0]
                with open(GREETD_CONF, "w") as fh:
                    fh.write(conf.rstrip("\n") + "\n")
        except OSError:
            pass
    remove_files([AUDIT_POLKIT, AUDIT_SUDO, AUDIT_FLAG])
    try:
        e = pwd.getpwnam(AUDIT_USER)
        log(f"deleting the account {AUDIT_USER}")
        run("userdel", "--remove", "--force", AUDIT_USER)
        if os.path.isdir(e.pw_dir) and e.pw_dir.startswith(("/var/home/", "/home/")):
            shutil.rmtree(e.pw_dir, ignore_errors=True)
    except KeyError:
        pass


def carry_out(plan):
    """Do plan (at shutdown, as root)."""
    log(f"{plan['mode']}: starting")
    if in_audit() and not plan.get("audit"):
        leave_audit()
    if plan.get("remove_users"):
        remove_users()
    if plan.get("generalize"):
        generalize()
    else:
        if plan.get("reset_network"):
            reset_network()
        if plan.get("mode") == "powerwash":
            remove_files(["/etc/hostname"])     # first-time setup names it again
    if plan.get("reset_bluetooth") and not plan.get("generalize"):
        reset_bluetooth()
    if plan.get("audit"):
        enter_audit()
    elif plan.get("oobe"):
        os.makedirs(os.path.dirname(OOBE_FLAG), exist_ok=True)
        with open(OOBE_FLAG, "w") as fh:
            fh.write(f"{plan['mode']} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        os.chmod(OOBE_FLAG, 0o644)
        log("first-time setup will show at the next start")
    log(f"{plan['mode']}: done")
