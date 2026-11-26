"""Access Work or School: join an organization's managed Firox computers
with the enrollment file they gave you, see what it manages, check for a new
policy, or leave. (firox-policy join [FILE]; Settings; double-clicking a
.firoxenroll file.)

The window reads the enrollment file itself, so before anything changes it
can show who's asking to manage the computer: the organization, where its
policies come from, and the fingerprint of the key that signs them, to
compare with what the organization told you. Joining, checking now and
leaving run `firox-policy enroll FILE`, `update` and `unenroll` as root
through pkexec, which asks for an administrator's password.
"""

import json
import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import firox_policies as P  # noqa: E402

POLICY = "/usr/bin/firox-policy"
ENROLLMENT = "/etc/firox/policy/enrollment.json"


def read_bundle(path):
    """What an enrollment file says ({organization, source, fingerprint,
    encrypted}), or raise ValueError in plain words. The real checks (the key
    matches its fingerprint) happen again as root when joining."""
    try:
        with open(path, encoding="utf-8") as fh:
            if os.fstat(fh.fileno()).st_size > 1 << 20:
                raise ValueError("That file is far too big to be an enrollment file.")
            bundle = json.load(fh)
    except OSError as e:
        raise ValueError(f"Couldn't open that file: {e.strerror}.") from None
    except ValueError:
        raise ValueError("That isn't a Firox enrollment file (it should end in .firoxenroll).") from None
    if not isinstance(bundle, dict) or bundle.get("format") != "firox-enrollment":
        raise ValueError("That isn't a Firox enrollment file (it should end in .firoxenroll).")
    if bundle.get("version") != 1:
        raise ValueError("That enrollment file needs a newer version of Firox. Update this computer first.")
    fpr = str(bundle.get("admin_fingerprint", "")).upper().replace(" ", "")
    if len(fpr) < 40 or not bundle.get("admin_key") or not bundle.get("source"):
        raise ValueError("That enrollment file is incomplete. Ask whoever gave it to you for a new one.")
    return {"organization": str(bundle.get("organization") or "").strip(), "source": str(bundle["source"]),
            "fingerprint": fpr, "encrypted": bool(bundle.get("machine_key"))}


def grouped(fpr):
    """A fingerprint as people compare them: ABCD 1234 …"""
    return " ".join(fpr[i:i + 4] for i in range(0, len(fpr), 4))


def managed():
    """(enrolled?, status dict)."""
    try:
        with open(P.MANAGED_JSON) as fh:
            status = json.load(fh)
    except (OSError, ValueError):
        status = {}
    return os.path.exists(ENROLLMENT) or bool(status.get("source")), status


def when(iso):
    if not iso:
        return "never"
    try:
        import datetime
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return t.strftime("%A %d %B %Y, %H:%M")
    except ValueError:
        return iso


class JoinWindow(Gtk.Window):
    def __init__(self, path=None):
        super().__init__(title="Access Work or School")
        self.set_default_size(580, 520)
        self.set_icon_name("network-workgroup")
        self.connect("destroy", Gtk.main_quit)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.box.set_border_width(18)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.box)
        self.add(sw)
        self.file = None
        self.show_page()
        self.show_all()
        if path:
            GLib.idle_add(lambda: self.choose(path) or False)

    # -- pages
    def clear(self):
        for c in self.box.get_children():
            c.destroy()

    def header(self, icon, title, text):
        head = Gtk.Box(spacing=14)
        head.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG), False, False, 0)
        lab = Gtk.Label(xalign=0, wrap=True, max_width_chars=56)
        lab.set_markup(f"<big><b>{GLib.markup_escape_text(title)}</b></big>\n{text}")
        head.pack_start(lab, True, True, 0)
        self.box.pack_start(head, False, False, 0)

    def fact(self, grid, row, label, value, mono=False):
        k = Gtk.Label(label=label, xalign=0, yalign=0)
        k.get_style_context().add_class("dim-label")
        v = Gtk.Label(xalign=0, wrap=True, max_width_chars=44, selectable=True)
        v.set_line_wrap_mode(2)
        v.set_markup(f"<tt>{GLib.markup_escape_text(value)}</tt>" if mono else GLib.markup_escape_text(value))
        grid.attach(k, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)

    def note_label(self):
        self.note = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
        self.box.pack_start(self.note, False, False, 0)

    def show_page(self):
        self.clear()
        enrolled, status = managed()
        if enrolled:
            self.page_managed(status)
        else:
            self.page_join()
        self.box.show_all()

    def page_join(self):
        self.header("network-workgroup", "Join your organization",
                    "If your school or workplace manages its Firox computers, they'll give you an "
                    "<b>enrollment file</b> (its name ends in <tt>.firoxenroll</tt>). Joining lets them look after "
                    "this computer for you: Wi-Fi, printers, updates, security and more.")
        pick = Gtk.Button(label="Choose Enrollment File…")
        pick.get_style_context().add_class("suggested-action")
        pick.set_halign(Gtk.Align.START)
        pick.connect("clicked", lambda *_: self.pick_file())
        self.box.pack_start(pick, False, False, 0)
        tip = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
        tip.set_markup("<small>You can also double-click the file in Firox Explorer. Got it by email? Save the "
                       "attachment first.</small>")
        tip.get_style_context().add_class("dim-label")
        self.box.pack_start(tip, False, False, 0)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.box.pack_start(self.details, False, False, 6)
        self.note_label()

    def page_managed(self, status):
        org = status.get("organization") or "your organization"
        self.header("network-workgroup", f"Managed by {org}",
                    "This computer follows the settings your organization chooses. They apply to everyone who "
                    "uses it, and can't be changed here while it's joined.")
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        row = 0
        for label, value, mono in (("Policies from", status.get("source", ""), True),
                                   ("Last checked", when(status.get("checked")), False),
                                   ("Policy in force",
                                    (f"number {status['serial']}, applied {when(status.get('applied'))}"
                                     if status.get("serial") is not None else "none yet"), False)):
            self.fact(grid, row, label, value, mono)
            row += 1
        self.box.pack_start(grid, False, False, 4)
        if status.get("error"):
            err = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
            err.set_markup(f"<b>The last check didn't work:</b> {GLib.markup_escape_text(status['error'])}. "
                           "It tries again every 30 minutes.")
            self.box.pack_start(err, False, False, 0)
        settings = status.get("settings") or {}
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<b>What it manages</b> ({len(settings)})" if settings else "<b>What it manages</b>")
        self.box.pack_start(t, False, False, 6)
        if not settings:
            self.box.pack_start(Gtk.Label(label="Nothing yet.", xalign=0), False, False, 0)
        for key, value in settings.items():
            pol = P.BY_KEY.get(key)
            line = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
            if pol is not None:
                where = str(getattr(pol, "category", "")).split("/")[-1]
                name = f"{where} › {pol.title}" if where and where.lower() not in pol.title.lower() else pol.title
                line.set_markup(f"• {GLib.markup_escape_text(name)}: "
                                f"{GLib.markup_escape_text(str(pol.describe(value)))}")
            else:
                line.set_text(f"• {key} (needs a newer Firox)")
            self.box.pack_start(line, False, False, 0)
        acts = Gtk.Box(spacing=8)
        check = Gtk.Button(label="Check for a New Policy Now")
        check.connect("clicked", lambda *_: self.run_root(["update"], "Checked."))
        acts.pack_start(check, False, False, 0)
        leave = Gtk.Button(label="Leave Organization…")
        leave.connect("clicked", lambda *_: self.leave(org))
        acts.pack_end(leave, False, False, 0)
        self.box.pack_start(acts, False, False, 10)
        self.note_label()

    # -- joining
    def pick_file(self):
        dlg = Gtk.FileChooserNative.new("Choose the Enrollment File", self, Gtk.FileChooserAction.OPEN, "_Open",
                                        "_Cancel")
        f = Gtk.FileFilter()
        f.set_name("Firox enrollment files")
        f.add_pattern("*.firoxenroll")
        dlg.add_filter(f)
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        dlg.add_filter(everything)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads and os.path.isdir(downloads):
            dlg.set_current_folder(downloads)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            self.choose(dlg.get_filename())
        dlg.destroy()

    def choose(self, path):
        for c in self.details.get_children():
            c.destroy()
        try:
            info = read_bundle(path)
        except ValueError as e:
            self.note.set_text(str(e))
            return
        self.file, self.info = os.path.abspath(path), info
        self.note.set_text("")
        org = info["organization"] or "an organization"
        t = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        t.set_markup(f"<b>Join {GLib.markup_escape_text(org)}?</b>")
        self.details.pack_start(t, False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        self.fact(grid, 0, "Organization", info["organization"] or "(no name given)")
        self.fact(grid, 1, "Policies from", info["source"], True)
        self.fact(grid, 2, "Signed with key", grouped(info["fingerprint"]), True)
        self.fact(grid, 3, "Policies", "encrypted for its computers" if info["encrypted"] else "signed, not encrypted")
        self.details.pack_start(grid, False, False, 0)
        warn = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
        warn.set_markup("Only join an organization you trust: it can set things for <b>everyone</b> who uses this "
                        "computer, and people can't change those settings here. If your organization gave you "
                        "a key fingerprint, check it matches the one above. You can leave at any time.")
        self.details.pack_start(warn, False, False, 0)
        acts = Gtk.Box(spacing=8)
        join = Gtk.Button(label=f"Join {org}")
        join.get_style_context().add_class("suggested-action")
        join.connect("clicked", lambda *_: self.run_root(["enroll", self.file], None))
        acts.pack_start(join, False, False, 0)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.show_page())
        acts.pack_start(cancel, False, False, 0)
        self.details.pack_start(acts, False, False, 4)
        self.details.show_all()

    def leave(self, org):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=f"Leave {org}?")
        dlg.format_secondary_text("This computer stops following its policies. The settings stay as they are, but "
                                  "anyone can change them again. Settings it added, like printers and Wi-Fi "
                                  "networks, are removed.")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Leave", Gtk.ResponseType.OK).get_style_context().add_class("destructive-action")
        if dlg.run() == Gtk.ResponseType.OK:
            self.run_root(["unenroll"], "Left the organization.")
        dlg.destroy()

    # -- doing it, as root
    def run_root(self, args, done):
        self.note.set_text("Waiting for an administrator's password…")
        for c in self.box.get_children():
            if isinstance(c, Gtk.Box):
                c.set_sensitive(False)

        def work():
            try:
                p = subprocess.run(["pkexec", POLICY, *args], capture_output=True, text=True, timeout=300)
                rc, out = p.returncode, (p.stdout + "\n" + p.stderr).strip()
            except (OSError, subprocess.SubprocessError) as e:
                rc, out = 1, str(e)
            GLib.idle_add(self.after_root, args[0], rc, out, done)
        threading.Thread(target=work, daemon=True).start()

    def after_root(self, cmd, rc, out, done):
        lines = [ln for ln in out.splitlines() if ln.strip()]
        last = lines[-1] if lines else ""
        self.show_page()
        if rc in (126, 127):
            text = "Nothing was changed (no administrator's password given)."
        elif cmd == "enroll":
            enrolled, _status = managed()
            if not enrolled:
                text = (last.replace("Not enrolled: ", "Couldn't join: ").rstrip(".") + "."
                        if last else "Couldn't join.")
            elif rc == 0:
                text = "Joined. Your organization's settings are now in force."
            else:
                text = (f"Joined, but its first policy couldn't be fetched yet ({last}). It tries again every "
                        "30 minutes; or click Check for a New Policy Now.")
        elif rc == 0:
            text = done or "Done."
        else:
            text = last or "It didn't work."
        self.note.set_text(text)
        return False


def run(path=None):
    JoinWindow(path)
    Gtk.main()
    return 0
