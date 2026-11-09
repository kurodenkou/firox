"""Network connections for Firox Explorer: Windows shares, SFTP, FTP,
WebDAV, Amazon S3 (and S3-compatible storage) and Backblaze B2, mounted in
your session only.

Security:
- connections are saved without secrets in ~/.config/firox/connections.json
  (mode 0600);
- passwords and keys go in your login keyring (gnome-keyring, through
  libsecret), unlocked when you log in, and only if you tick "Remember";
- Windows shares, SFTP, FTP and WebDAV are mounted by gvfs (your session's
  own GIO daemon); SFTP asks before trusting a new server key;
- S3 and B2 are mounted by rclone (FUSE) in a systemd user unit that's part
  of the desktop session, so it's unmounted when you log off. Its keys are
  passed in the unit's environment (kept in your private runtime folder,
  /run/user/UID), never on a command line;
- mounts are yours alone (FUSE without allow_other), under
  /run/user/UID/firox-mounts (S3/B2) and /run/user/UID/gvfs (the rest).

Logs: each S3/B2 mount writes ~/.local/state/firox/mounts/ID.log (rclone,
INFO level: no keys). diagnose() (firox-explorer --test NAME) checks a
connection step by step and writes ID-test.log, with any secret blanked.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret  # noqa: E402
except (ValueError, ImportError):
    Secret = None

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "firox")
STORE = os.path.join(CONFIG, "connections.json")
LOGDIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                      "firox", "mounts")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
MOUNTS = os.path.join(RUNTIME, "firox-mounts")

# kind: (label, icon, backend, default port)
KINDS = {
    "smb": ("Windows share (SMB/CIFS)", "folder-remote", "gvfs", 445),
    "sftp": ("SFTP (SSH)", "folder-remote", "gvfs", 22),
    "ftp": ("FTP", "folder-remote", "gvfs", 21),
    "ftps": ("FTP with TLS (FTPS)", "folder-remote", "gvfs", 21),
    "dav": ("WebDAV", "folder-remote", "gvfs", 80),
    "davs": ("WebDAV over HTTPS", "folder-remote", "gvfs", 443),
    "s3": ("Amazon S3 or compatible", "network-server", "rclone", 0),
    "b2": ("Backblaze B2", "network-server", "rclone", 0),
}
S3_PROVIDERS = [("AWS", "Amazon S3"), ("Wasabi", "Wasabi"), ("Cloudflare", "Cloudflare R2"),
                ("Minio", "MinIO"), ("Ceph", "Ceph"), ("Other", "Other S3-compatible")]

SCHEMA = None
if Secret is not None:
    SCHEMA = Secret.Schema.new("org.firox.Connection", Secret.SchemaFlags.NONE,
                               {"connection": Secret.SchemaAttributeType.STRING})


# ---------------------------------------------------------- saved list --

def load():
    try:
        with open(STORE) as fh:
            data = json.load(fh)
        return [c for c in data if isinstance(c, dict) and c.get("id") and c.get("kind") in KINDS]
    except (OSError, ValueError):
        return []


def save(conns):
    os.makedirs(CONFIG, exist_ok=True)
    tmp = STORE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(conns, fh, indent=1)
    os.replace(tmp, STORE)


def new_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "connection"
    return f"{slug}-{int(time.time() * 1000) % 100000000:x}"


def upsert(conn):
    conns = [c for c in load() if c["id"] != conn["id"]] + [conn]
    save(conns)


def remove(conn):
    unmount(conn)
    forget_secret(conn)
    save([c for c in load() if c["id"] != conn["id"]])


# -------------------------------------------------------------- secrets --

def keyring_available():
    return SCHEMA is not None


def store_secret(conn, secret):
    """secret: dict (password / keys). Kept in the login keyring."""
    if SCHEMA is None:
        return False
    try:
        return Secret.password_store_sync(SCHEMA, {"connection": conn["id"]}, Secret.COLLECTION_DEFAULT,
                                          f"Firox connection: {conn.get('name', conn['id'])}",
                                          json.dumps(secret), None)
    except GLib.Error as err:
        print(f"firox_mounts: keyring: {err.message}")
        return False


def lookup_secret(conn):
    if SCHEMA is None:
        return None
    try:
        text = Secret.password_lookup_sync(SCHEMA, {"connection": conn["id"]}, None)
    except GLib.Error:
        return None
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def forget_secret(conn):
    if SCHEMA is None:
        return
    try:
        Secret.password_clear_sync(SCHEMA, {"connection": conn["id"]}, None)
    except GLib.Error:
        pass


def secret_fields(kind):
    """The secret values a kind needs: [(key, label)]."""
    if kind == "s3":
        return [("secret_access_key", "Secret access key")]
    if kind == "b2":
        return [("key", "Application key")]
    return [("password", "Password")]


# ---------------------------------------------------------------- gvfs --

def uri(conn):
    """The gvfs address of a Windows share / SFTP / FTP / WebDAV connection."""
    kind = conn["kind"]
    host = conn.get("host", "").strip()
    port = str(conn.get("port") or "").strip()
    user = conn.get("user", "").strip()
    path = "/" + conn.get("path", "").strip().strip("/")
    if kind == "smb":
        share = conn.get("share", "").strip().strip("/")
        path = "/" + share + (path if path != "/" else "")
        domain = conn.get("domain", "").strip()
        if user and domain:
            user = f"{domain};{user}"
    scheme = kind
    auth = urllib.parse.quote(user, safe=";") + "@" if user else ""
    hostport = host + (f":{port}" if port and int(port) != KINDS[kind][3] else "")
    return f"{scheme}://{auth}{hostport}{urllib.parse.quote(path)}"


def gvfs_mount_for(conn):
    try:
        return Gio.File.new_for_uri(uri(conn)).find_enclosing_mount(None)
    except GLib.Error:
        return None


# --------------------------------------------------------------- rclone --

def rclone_unit(conn):
    return "firox-mount-" + re.sub(r"[^a-zA-Z0-9]", "-", conn["id"])


def mountpoint(conn):
    """Where an S3/B2 connection appears (private to you)."""
    safe = re.sub(r"[/\0]", "-", conn.get("name") or conn["id"]).strip() or conn["id"]
    return os.path.join(MOUNTS, safe)


def rclone_env(conn, secret):
    """rclone's settings for the remote "firox" as environment variables."""
    env = {}

    def put(key, value):
        if value not in (None, ""):
            env[f"RCLONE_CONFIG_FIROX_{key.upper()}"] = str(value)
    if conn["kind"] == "s3":
        put("type", "s3")
        put("provider", conn.get("provider") or "AWS")
        put("access_key_id", conn.get("user"))
        put("secret_access_key", secret.get("secret_access_key"))
        put("region", conn.get("region"))
        put("endpoint", conn.get("endpoint"))
        put("env_auth", "false")
    else:
        put("type", "b2")
        put("account", conn.get("user"))
        put("key", secret.get("key"))
    return env


def log_path(conn, suffix=""):
    os.makedirs(LOGDIR, mode=0o700, exist_ok=True)
    return os.path.join(LOGDIR, re.sub(r"[^a-zA-Z0-9_.-]", "-", conn["id"]) + suffix + ".log")


def log_problem(path, fallback):
    """The useful end of an rclone log: its error lines (or the last
    lines), plus where the whole log is."""
    try:
        with open(path, errors="replace") as fh:
            lines = [ln.rstrip() for ln in fh if ln.strip()]
    except OSError:
        lines = []
    errors = [ln for ln in lines if re.search(r"\b(ERROR|CRITICAL|Fatal|NOTICE: .*fail)", ln)]
    shown = (errors or lines)[-6:]
    text = "\n".join(re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d ", "", ln) for ln in shown) or fallback
    return f"{text}\n\nThe whole log: {path}\n(right-click the connection > Test Connection for more)"


def rclone_target(conn):
    bucket = conn.get("bucket", "").strip().strip("/")
    path = conn.get("path", "").strip().strip("/")
    return "firox:" + "/".join(p for p in (bucket, path) if p)


# ---------------------------------------------------------- mount state --

def is_mounted(conn):
    if KINDS[conn["kind"]][2] == "rclone":
        return os.path.ismount(mountpoint(conn))
    return gvfs_mount_for(conn) is not None


def location(conn):
    """A Gio.File for the connection's top folder (when mounted)."""
    if KINDS[conn["kind"]][2] == "rclone":
        return Gio.File.new_for_path(mountpoint(conn))
    return Gio.File.new_for_uri(uri(conn))


def mount_rclone(conn, secret, done):
    """Start rclone in a user unit tied to the desktop session; calls
    done(error or None) once it's mounted (or failed)."""
    if shutil.which("rclone") is None:
        done("rclone isn't installed (it comes with Firox Explorer's image update).")
        return
    point = mountpoint(conn)
    os.makedirs(MOUNTS, mode=0o700, exist_ok=True)
    os.chmod(MOUNTS, 0o700)
    os.makedirs(point, exist_ok=True)
    unit = rclone_unit(conn)
    log = log_path(conn)
    try:
        os.remove(log)                    # rclone appends; one log per connect
    except OSError:
        pass
    rclone_args = ["rclone", "mount", rclone_target(conn), point,
                   "--vfs-cache-mode", "writes", "--dir-cache-time", "30s", "--umask", "077",
                   "--log-file", log, "--log-level", "INFO"]
    if not systemd_user():
        # No systemd user manager: run it ourselves (still only in the
        # environment, never on the command line)
        mount_rclone_direct(conn, secret, rclone_args, point, done)
        return
    subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
    argv = ["systemd-run", "--user", "--quiet", f"--unit={unit}", "--collect",
            "--property=PartOf=firox-session.target", "--property=After=firox-session.target",
            f"--description=Firox Explorer: {conn.get('name', conn['id'])}"]
    # Only the names go on the command line (anyone can read command
    # lines); systemd-run takes the values from its own environment
    secrets = rclone_env(conn, secret)
    for k in secrets:
        argv.append(f"--setenv={k}")
    argv += rclone_args
    r = subprocess.run(argv, capture_output=True, text=True, env={**os.environ, **secrets})
    if r.returncode != 0:
        done((r.stderr or "couldn't start rclone").strip())
        return
    started = time.monotonic()

    def check():
        if os.path.ismount(point):
            verify_mount(conn, point, log, done)
            return False
        active = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True,
                                text=True).stdout.strip()
        if active in ("failed", "inactive") or time.monotonic() - started > 20:
            journal = subprocess.run(["journalctl", "--user", "-u", unit, "-n", "8", "--no-pager", "-o", "cat"],
                                     capture_output=True, text=True).stdout.strip()
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
            if journal and not os.path.exists(log):     # rclone didn't get as far as its log
                with open(log, "w") as fh:
                    fh.write(journal + "\n")
            done(log_problem(log, "rclone stopped before the storage was mounted"
                             if active != "activating" else "timed out after 20 seconds"))
            return False
        return True
    GLib.timeout_add(250, check)


def verify_mount(conn, point, log, done):
    """rclone mounts S3 before it has tried the keys: list the folder once
    (in a thread, it can take a while) and give up with rclone's error if
    that fails, rather than leaving a folder that only says I/O error."""
    def work():
        try:
            os.listdir(point)
            err = None
        except OSError as e:
            err = e
        GLib.idle_add(finish, err)

    def finish(err):
        if err is None:
            done(None)
        else:
            unmount(conn)
            done(log_problem(log, f"The storage was mounted but couldn't be read ({err.strerror})."))
        return False
    threading.Thread(target=work, daemon=True).start()


def systemd_user():
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mount_rclone_direct(conn, secret, argv, point, done):
    log = log_path(conn)
    try:
        proc = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env={**os.environ, **rclone_env(conn, secret)})
    except OSError as err:
        done(str(err))
        return
    started = time.monotonic()

    def check():
        if os.path.ismount(point):
            verify_mount(conn, point, log, done)
            return False
        if proc.poll() is not None or time.monotonic() - started > 20:
            if proc.poll() is None:
                proc.terminate()
            done(log_problem(log, "rclone stopped before the storage was mounted"))
            return False
        return True
    GLib.timeout_add(250, check)


def mount_gvfs(conn, secret, ask, done):
    """Mount with gvfs. secret: {"password": ...} or None to ask.
    ask(kind, *args) shows a question: ("password", message) -> (user, password, remember)
    or None; ("question", message, choices) -> index or None."""
    op = Gio.MountOperation()

    def on_password(op, message, default_user, default_domain, flags):
        if secret and secret.get("password") is not None and not getattr(op, "_tried", False):
            op._tried = True
            op.set_username(conn.get("user") or default_user)
            if conn.get("domain"):
                op.set_domain(conn["domain"])
            op.set_password(secret["password"])
            op.set_password_save(Gio.PasswordSave.NEVER)
            op.reply(Gio.MountOperationResult.HANDLED)
            return
        if flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED and not conn.get("user"):
            op.set_anonymous(True)
            op.reply(Gio.MountOperationResult.HANDLED)
            return
        answer = ask("password", message)
        if answer is None:
            op.reply(Gio.MountOperationResult.ABORTED)
            return
        user, password, remember = answer
        op.set_username(user or default_user)
        if conn.get("domain"):
            op.set_domain(conn["domain"])
        op.set_password(password)
        op.set_password_save(Gio.PasswordSave.NEVER)
        if remember:
            store_secret(conn, {"password": password})
        op.reply(Gio.MountOperationResult.HANDLED)

    def on_question(op, message, choices):
        # e.g. "The identity of the server is unknown... (fingerprint)"
        choice = ask("question", message, list(choices))
        if choice is None:
            op.reply(Gio.MountOperationResult.ABORTED)
        else:
            op.set_choice(choice)
            op.reply(Gio.MountOperationResult.HANDLED)

    op.connect("ask-password", on_password)
    op.connect("ask-question", on_question)

    def finished(gfile, result):
        try:
            gfile.mount_enclosing_volume_finish(result)
            done(None)
        except GLib.Error as err:
            if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                done(None)
            elif err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                done("")            # cancelled by you
            else:
                done(err.message)
    Gio.File.new_for_uri(uri(conn)).mount_enclosing_volume(Gio.MountMountFlags.NONE, op, None, finished)


def mount(conn, ask, done):
    """Connect: uses the remembered secret, else asks."""
    secret = lookup_secret(conn) or {}
    if KINDS[conn["kind"]][2] == "rclone":
        if not all(secret.get(k) for k, _ in secret_fields(conn["kind"])):
            answer = ask("keys", conn)
            if answer is None:
                done("")
                return
            secret, remember = answer
            if remember:
                store_secret(conn, secret)
        mount_rclone(conn, secret, done)
    else:
        mount_gvfs(conn, secret, ask, done)


def unmount(conn, done=None):
    if KINDS[conn["kind"]][2] == "rclone":
        subprocess.run(["systemctl", "--user", "stop", rclone_unit(conn)], capture_output=True)
        if os.path.ismount(mountpoint(conn)):
            subprocess.run(["fusermount3", "-u", mountpoint(conn)], capture_output=True)
        try:
            os.rmdir(mountpoint(conn))
        except OSError:
            pass
        if done:
            done(None)
        return
    m = gvfs_mount_for(conn)
    if m is None:
        if done:
            done(None)
        return

    def finished(mount, result):
        try:
            mount.unmount_with_operation_finish(result)
            if done:
                done(None)
        except GLib.Error as err:
            if done:
                done(err.message)
    m.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, finished)


# ------------------------------------------------------------ diagnosis --

def find(name_or_id):
    for conn in load():
        if name_or_id in (conn["id"], conn.get("name")):
            return conn
    low = name_or_id.casefold()
    for conn in load():
        if (conn.get("name") or "").casefold() == low:
            return conn
    return None


def diagnose(conn, out=print, ask_secret=None):
    """Check a connection step by step, printing a report through out()
    (secret values replaced by ***). Writes ID-test.log too; returns its
    path. ask_secret(fields) -> dict, when nothing is remembered."""
    path = log_path(conn, "-test")
    report = []
    secrets = []

    def say(text=""):
        for value in secrets:
            if value and len(value) >= 4:
                text = text.replace(value, "***")
        report.append(text)
        out(text)

    def run(argv, env=None, timeout=60):
        say("$ " + " ".join(argv))
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               env={**os.environ, **(env or {})})
            body = (r.stdout + r.stderr).rstrip()
            code = r.returncode
        except subprocess.TimeoutExpired as err:
            body = ((err.stdout or b"").decode(errors="replace") + (err.stderr or b"").decode(errors="replace"))
            body = body.rstrip() + f"\n(stopped after {timeout} s)"
            code = -1
        except OSError as err:
            body, code = str(err), -1
        for ln in body.splitlines()[-80:]:
            say("    " + ln)
        say(f"    -> exit code {code}")
        return code

    kind = conn["kind"]
    say(f"Firox Explorer connection test, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"Connection: {conn.get('name')} (id {conn['id']}), type {KINDS[kind][0]}")
    shown = {k: v for k, v in conn.items() if k not in ("id", "name") and v not in ("", None)}
    if shown.get("user") and kind in ("s3", "b2"):
        u = shown["user"]
        shown["user"] = u[:4] + "…" + u[-2:] if len(u) > 8 else u
    say("Settings: " + json.dumps(shown))
    say()
    say("1. Secret")
    say(f"    keyring (libsecret): {'available' if keyring_available() else 'NOT available'}")
    secret = lookup_secret(conn) or {}
    needed = [k for k, _l in secret_fields(kind)]
    if all(secret.get(k) for k in needed):
        say("    remembered in the keyring: yes")
    else:
        say("    remembered in the keyring: no")
        if ask_secret is not None:
            secret = ask_secret(secret_fields(kind)) or {}
    secrets.extend(str(v) for v in secret.values() if v)
    say()
    if KINDS[kind][2] == "rclone":
        say("2. Tools")
        run(["rclone", "version"], timeout=20)
        for tool in ("fusermount3", "fusermount", "systemd-run"):
            say(f"    {tool}: {shutil.which(tool) or 'not found'}")
        say(f"    /dev/fuse: {'present' if os.path.exists('/dev/fuse') else 'MISSING'}"
            f"{', usable' if os.access('/dev/fuse', os.R_OK | os.W_OK) else ', not usable by you'}")
        say(f"    systemd user manager: {'yes' if systemd_user() else 'no'}")
        say()
        env = rclone_env(conn, secret)
        def shown_value(k, v):
            if k.endswith(("_KEY", "_SECRET_ACCESS_KEY")):
                return "***"
            if k.endswith(("_ACCOUNT", "_ACCESS_KEY_ID")) and len(v) > 8:
                return v[:4] + "…" + v[-2:]
            return v
        say("3. Settings rclone gets (keys hidden): " + ", ".join(f"{k}={shown_value(k, v)}" for k, v in env.items()))
        say()
        say("4. Listing the bucket (no mount), verbose")
        code = run(["rclone", "lsf", rclone_target(conn), "--max-depth", "1", "-vv",
                    "--retries", "1", "--low-level-retries", "2", "--contimeout", "15s", "--timeout", "30s"],
                   env=env, timeout=90)
        if code != 0 and kind == "b2":
            say("    Listing failed. Can the key see the account's buckets at all?")
            run(["rclone", "lsd", "firox:", "-vv", "--retries", "1", "--low-level-retries", "2"],
                env=env, timeout=60)
        say()
        say("5. Trying a mount for 10 seconds, verbose")
        if shutil.which("rclone") is None:
            say("    skipped: rclone isn't installed (update Firox Linux to get it)")
        elif is_mounted(conn):
            say(f"    already mounted at {mountpoint(conn)}: skipped")
        else:
            os.makedirs(MOUNTS, mode=0o700, exist_ok=True)
            point = os.path.join(MOUNTS, ".test-" + conn["id"])
            os.makedirs(point, exist_ok=True)
            mlog = log_path(conn, "-test-mount")
            try:
                os.remove(mlog)
            except OSError:
                pass
            say(f"$ rclone mount {rclone_target(conn)} {point} -vv")
            proc = subprocess.Popen(["rclone", "mount", rclone_target(conn), point, "--vfs-cache-mode", "writes",
                                     "--umask", "077", "-vv", "--log-file", mlog],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env={**os.environ, **env})
            ok = False
            for _ in range(40):
                time.sleep(0.25)
                if os.path.ismount(point):
                    ok = True
                    break
                if proc.poll() is not None:
                    break
            if ok:
                try:
                    names = sorted(os.listdir(point))[:10]
                    say(f"    MOUNTED. First items: {names}")
                except OSError as err:
                    say(f"    mounted, but listing failed: {err}")
                subprocess.run(["fusermount3", "-u", point], capture_output=True)
            else:
                say("    did NOT mount" + (f" (rclone exited with {proc.returncode})" if proc.poll() is not None
                                         else " within 10 seconds"))
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                with open(mlog, errors="replace") as fh:
                    for ln in fh.read().splitlines()[-60:]:
                        say("    " + ln)
            except OSError:
                pass
            try:
                os.rmdir(point)
            except OSError:
                pass
        say()
        say("6. The last real connect (Firox Explorer's own log)")
        try:
            with open(log_path(conn), errors="replace") as fh:
                for ln in fh.read().splitlines()[-30:]:
                    say("    " + ln)
        except OSError:
            say("    (none yet)")
        run(["journalctl", "--user", "-u", rclone_unit(conn), "-n", "20", "--no-pager", "-o", "short"], timeout=20)
    else:
        say("2. Address")
        say(f"    {uri(conn)}")
        say(f"    mounted now: {'yes' if is_mounted(conn) else 'no'}")
        run(["gio", "mount", "-l"], timeout=20)
        say()
        say("3. Can this computer find the server?")
        host = conn.get("host", "")
        run(["getent", "hosts", host], timeout=15)
        port = int(conn.get("port") or KINDS[kind][3] or 0)
        if port:
            import socket
            try:
                with socket.create_connection((host, port), timeout=8):
                    say(f"    port {port}: open")
            except OSError as err:
                say(f"    port {port}: can't connect ({err})")
        say()
        say("4. gvfs messages from the last 10 minutes")
        run(["journalctl", "--user", "--since", "-10min", "--no-pager", "-o", "short", "-t", "gvfsd",
             "-t", "gvfsd-smb", "-t", "gvfsd-sftp", "-t", "gvfsd-ftp", "-t", "gvfsd-dav", "-t", "gvfsd-smb-browse"],
            timeout=20)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(report) + "\n")
    except OSError:
        pass
    return path
