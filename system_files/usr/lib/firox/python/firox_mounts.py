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
"""

import json
import os
import re
import shutil
import subprocess
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
    rclone_args = ["rclone", "mount", rclone_target(conn), point,
                   "--vfs-cache-mode", "writes", "--dir-cache-time", "30s", "--umask", "077",
                   "--volname", conn.get("name") or "Cloud"]
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
            done(None)
            return False
        active = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True,
                                text=True).stdout.strip()
        if active in ("failed", "inactive") or time.monotonic() - started > 20:
            log = subprocess.run(["journalctl", "--user", "-u", unit, "-n", "8", "--no-pager", "-o", "cat"],
                                 capture_output=True, text=True).stdout.strip()
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
            done(log.splitlines()[-1] if log else "rclone stopped before the storage was mounted")
            return False
        return True
    GLib.timeout_add(250, check)


def systemd_user():
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mount_rclone_direct(conn, secret, argv, point, done):
    log = os.path.join(MOUNTS, f".{conn['id']}.log")
    try:
        out = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        proc = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=out, env={**os.environ, **rclone_env(conn, secret)})
        os.close(out)
    except OSError as err:
        done(str(err))
        return
    started = time.monotonic()

    def check():
        if os.path.ismount(point):
            done(None)
            return False
        if proc.poll() is not None or time.monotonic() - started > 20:
            if proc.poll() is None:
                proc.terminate()
            try:
                with open(log) as fh:
                    lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            except OSError:
                lines = []
            done(lines[-1] if lines else "rclone stopped before the storage was mounted")
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
