"""The Firox policy editor (firox-policy with no arguments).

Write a policy (each setting On, Off, a number, or Not configured), sign it
with your admin key, and save it where your computers fetch it. Also makes
the admin key itself and the enrollment files computers are enrolled with.
The draft and the key choices live in ~/.config/firox/policy-editor.json;
the keys in ~/.local/share/firox/policy-admin/gnupg.
"""

import os
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import firox_policies as P  # noqa: E402


def run(fp):
    conf = fp.read_json(fp.EDITOR_CONF, {}) or {}

    def save_conf():
        fp.write_json(fp.EDITOR_CONF, conf, 0o600)

    def message(parent, kind, text, secondary=""):
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind,
                                buttons=Gtk.ButtonsType.OK, text=text)
        if secondary:
            dlg.format_secondary_markup(secondary)
        dlg.set_title("Policy Editor")
        dlg.run()
        dlg.destroy()

    def margins(widget, n=14):
        for side in ("start", "end", "top", "bottom"):
            getattr(widget, f"set_margin_{side}")(n)

    def ask(parent, title, text, fields, ok="OK"):
        """A small form: fields = [(label, initial, secret)] -> values or None."""
        dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, ok, Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        margins(box)
        lbl = Gtk.Label(xalign=0, wrap=True, max_width_chars=56)
        lbl.set_markup(text)
        box.add(lbl)
        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        entries = []
        for i, (label, initial, secret) in enumerate(fields):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            e = Gtk.Entry(text=initial or "", visibility=not secret, activates_default=True, hexpand=True)
            grid.attach(e, 1, i, 1, 1)
            entries.append(e)
        box.add(grid)
        dlg.show_all()
        values = [e.get_text() for e in entries] if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return values

    def choose_file(parent, title, action, name=None, patterns=()):
        dlg = Gtk.FileChooserNative.new(title, parent, action, None, None)
        if name:
            dlg.set_current_name(name)
            dlg.set_do_overwrite_confirmation(True)
        folder = conf.get("folder") or os.path.expanduser("~")
        if os.path.isdir(folder):
            dlg.set_current_folder(folder)
        for label, pattern in patterns:
            f = Gtk.FileFilter()
            f.set_name(label)
            f.add_pattern(pattern)
            dlg.add_filter(f)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.ACCEPT else None
        dlg.destroy()
        if path:
            conf["folder"] = os.path.dirname(path)
            save_conf()
        return path

    class Editor(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Policy Editor")
            self.set_default_size(640, 720)
            self.set_icon_name("preferences-system")
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            margins(outer)
            self.add(outer)

            intro = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
            intro.set_markup("<b>Manage your Firox computers from one place</b>\n"
                             "Choose settings, then <i>Save and Sign</i>: your computers fetch the signed "
                             "policy and apply it, and they only accept policies signed with your admin key. "
                             "Settings left <i>Not configured</i> stay up to each person.")
            outer.add(intro)

            # -- the admin key
            keybox = Gtk.Box(spacing=8)
            self.key_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
            keybox.add(self.key_label)
            self.key_button = Gtk.Button()
            self.key_button.connect("clicked", self.on_key_button)
            keybox.add(self.key_button)
            outer.add(keybox)

            org = Gtk.Box(spacing=8)
            org.add(Gtk.Label(label="Organization:"))
            self.org = Gtk.Entry(hexpand=True, placeholder_text="shown on managed computers, e.g. Home Lab",
                                 text=conf.get("organization", ""))
            org.add(self.org)
            outer.add(org)

            # -- the settings
            grid = Gtk.Grid(row_spacing=12, column_spacing=16)
            margins(grid, 10)
            self.controls = {}
            settings = conf.get("settings") or {}
            for row, pol in enumerate(P.POLICIES):
                text = Gtk.Label(xalign=0, wrap=True, max_width_chars=48, hexpand=True)
                text.set_markup(f"<b>{GLib.markup_escape_text(pol.title)}</b>\n"
                                f"<small>{GLib.markup_escape_text(pol.description)}</small>")
                grid.attach(text, 0, row, 1, 1)
                value = settings.get(pol.key)
                if pol.kind == "bool":
                    combo = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
                    for item in ("Not configured", pol.on_label, pol.off_label):
                        combo.append_text(item)
                    combo.set_active(0 if value is None else (1 if value else 2))
                    grid.attach(combo, 1, row, 1, 1)
                    self.controls[pol.key] = combo
                else:
                    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                    combo = Gtk.ComboBoxText()
                    for item in ("Not configured", "Set to"):
                        combo.append_text(item)
                    spin = Gtk.SpinButton.new_with_range(pol.low, pol.high, 1)
                    spin.set_value(value if isinstance(value, int) else min(max(10, pol.low), pol.high))
                    combo.connect("changed", lambda c, s=spin: s.set_sensitive(c.get_active() == 1))
                    combo.set_active(0 if value is None else 1)
                    spin.set_sensitive(value is not None)
                    box.add(combo)
                    box.add(spin)
                    if pol.unit:
                        box.add(Gtk.Label(label=pol.unit))
                    grid.attach(box, 1, row, 1, 1)
                    self.controls[pol.key] = (combo, spin)
            scroll = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.add(grid)
            outer.add(scroll)

            self.encrypt = Gtk.CheckButton(label="Encrypt the policy (only enrolled computers can read it)")
            self.encrypt.set_active(bool(conf.get("encrypt")))
            outer.add(self.encrypt)
            self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
            self.status.get_style_context().add_class("dim-label")
            outer.add(self.status)

            buttons = Gtk.Box(spacing=6)
            for label, handler in (("Open…", self.on_open), ("Enrollment File…", self.on_enroll_file)):
                b = Gtk.Button(label=label)
                b.connect("clicked", handler)
                buttons.add(b)
            self.sign = Gtk.Button(label="Save and Sign…")
            self.sign.get_style_context().add_class("suggested-action")
            self.sign.connect("clicked", self.on_sign)
            buttons.pack_end(self.sign, False, False, 0)
            outer.add(buttons)
            self.connect("delete-event", lambda *_a: self.keep_draft() or False)
            self.refresh_key()

        # -- the form
        def values(self):
            out = {}
            for pol in P.POLICIES:
                c = self.controls[pol.key]
                if pol.kind == "bool":
                    if c.get_active() in (1, 2):
                        out[pol.key] = c.get_active() == 1
                elif c[0].get_active() == 1:
                    out[pol.key] = int(c[1].get_value())
            return out

        def load(self, settings, organization=None):
            if organization is not None:
                self.org.set_text(organization)
            for pol in P.POLICIES:
                c, value = self.controls[pol.key], settings.get(pol.key)
                if pol.kind == "bool":
                    c.set_active(0 if value is None else (1 if value else 2))
                else:
                    c[0].set_active(0 if value is None else 1)
                    if isinstance(value, int):
                        c[1].set_value(value)

        def keep_draft(self):
            conf.update(organization=self.org.get_text().strip(), settings=self.values(),
                        encrypt=self.encrypt.get_active())
            save_conf()

        # -- keys
        def refresh_key(self):
            keys = fp.admin_keys()
            self.keys = keys
            if keys["sign"]:
                f = keys["sign"]
                pretty = " ".join(f[i:i + 4] for i in range(0, len(f), 4))
                self.key_label.set_markup(f"<b>Admin key:</b> <tt>{pretty}</tt>")
                self.key_button.set_label("Export Public Key…")
            else:
                self.key_label.set_markup("<b>No admin key yet.</b> Make one to sign policies: it stays on "
                                          "this computer.")
                self.key_button.set_label("Create Admin Key…")
            self.sign.set_sensitive(bool(keys["sign"]))

        def on_key_button(self, _b):
            if self.keys["sign"]:
                path = choose_file(self, "Export Public Key", Gtk.FileChooserAction.SAVE, "firox-admin-key.asc")
                if path:
                    with open(path, "w") as fh:
                        fh.write(fp.export_key(self.keys["sign"]))
                    self.status.set_text(f"Saved the public half of your admin key to {path}.")
                return
            org = self.org.get_text().strip() or "Firox"
            values = ask(self, "Create Admin Key",
                         "Your admin key signs every policy. Computers you enroll trust <b>only</b> this key, "
                         "so keep this computer (and a backup of "
                         "<tt>~/.local/share/firox/policy-admin</tt>) safe.\n\n"
                         "A passphrase protects the key if someone copies it: you'll type it each time you "
                         "sign.", [("Passphrase:", "", True), ("Again:", "", True)], ok="Create")
            if values is None:
                return
            if values[0] != values[1]:
                message(self, Gtk.MessageType.ERROR, "The passphrases don't match.")
                return
            if not values[0]:
                message(self, Gtk.MessageType.WARNING, "No passphrase",
                        "Anyone who copies the key can sign policies for your computers.")
            try:
                conf["sign_key"] = fp.new_key(f"Firox Policy Admin ({org})", "sign", values[0])
            except RuntimeError as e:
                message(self, Gtk.MessageType.ERROR, "Couldn't make the key", GLib.markup_escape_text(str(e)))
                return
            save_conf()
            self.refresh_key()
            self.status.set_text("Admin key made. Next: choose settings, then Save and Sign.")

        def machines_key(self):
            if self.keys["machines"]:
                return self.keys["machines"]
            org = self.org.get_text().strip() or "Firox"
            conf["machines_key"] = fp.new_key(f"Firox Policy Machines ({org})", "encr", "")
            save_conf()
            self.refresh_key()
            return conf["machines_key"]

        # -- signing
        def on_sign(self, _b):
            self.keep_draft()
            settings = self.values()
            if not settings and not self.org.get_text().strip():
                message(self, Gtk.MessageType.INFO, "Nothing to sign",
                        "Set at least one setting (or give your organization a name).")
                return
            values = ask(self, "Sign the Policy", "Type your admin key's passphrase to sign this policy "
                         "(leave it empty if the key has none).", [("Passphrase:", "", True)], ok="Sign")
            if values is None:
                return
            path = choose_file(self, "Save the Signed Policy", Gtk.FileChooserAction.SAVE,
                               os.path.basename(conf.get("output") or "policy.firoxpol"),
                               [("Firox policies", "*.firoxpol")])
            if not path:
                return
            policy = {"format": fp.POLICY_FORMAT, "version": fp.VERSION,
                      "organization": self.org.get_text().strip(),
                      # the signing time: always newer than the last policy, from any editor
                      "serial": int(time.time()), "issued": fp.now_iso(), "settings": settings}
            try:
                machines = self.machines_key() if self.encrypt.get_active() else None
                data = fp.sign_policy(policy, self.keys["sign"], values[0], machines)
            except RuntimeError as e:
                message(self, Gtk.MessageType.ERROR, "Couldn't sign the policy", GLib.markup_escape_text(str(e)))
                return
            with open(path, "wb") as fh:
                fh.write(data)
            conf["output"] = path
            save_conf()
            where = conf.get("source") or "the address in your enrollment file"
            self.status.set_text(f"Signed policy saved to {path}. Publish it at {where}: your computers "
                                 "pick it up within 30 minutes (sudo firox-policy update does it now).")

        def on_open(self, _b):
            path = choose_file(self, "Open a Policy", Gtk.FileChooserAction.OPEN, None,
                               [("Firox policies", "*.firoxpol"), ("All files", "*")])
            if not path:
                return
            try:
                with open(path, "rb") as fh:
                    data = fh.read(fp.MAX_POLICY + 1)
                policy = fp.open_policy(data, fp.ADMIN_KEYRING, self.keys["sign"] or "-")
            except (OSError, fp.Refused) as e:
                message(self, Gtk.MessageType.ERROR, "Couldn't open that policy",
                        GLib.markup_escape_text(str(e)))
                return
            self.load(policy.get("settings") or {}, policy.get("organization", ""))
            self.status.set_text(f"Opened {os.path.basename(path)} (signed {policy.get('issued', '?')}).")

        # -- enrollment
        def on_enroll_file(self, _b):
            if not self.keys["sign"]:
                message(self, Gtk.MessageType.INFO, "Make an admin key first")
                return
            values = ask(self, "Enrollment File",
                         "Computers enrolled with this file fetch the policy from this address and trust "
                         "only your admin key.\n\n"
                         "<tt>https://example.com/policy.firoxpol</tt>  a web server\n"
                         "<tt>git+https://host/repo.git#policy.firoxpol</tt>  a git repository\n"
                         "<tt>/mnt/share/policy.firoxpol</tt>  a file or network share",
                         [("Policy address:", conf.get("source", ""), False)], ok="Next")
            if not values or not values[0].strip():
                return
            source = values[0].strip()
            if not source.startswith(("https://", "git+https://", "git+file://", "file://", "/")):
                message(self, Gtk.MessageType.ERROR, "That address won't work",
                        "Use an https:// address, a git+https:// repository or a file path. Plain http:// "
                        "isn't accepted.")
                return
            conf["source"] = source
            save_conf()
            encrypted = self.encrypt.get_active()
            try:
                bundle = {"format": fp.ENROLL_FORMAT, "version": fp.VERSION,
                          "organization": self.org.get_text().strip(), "source": source,
                          "admin_fingerprint": self.keys["sign"], "admin_key": fp.export_key(self.keys["sign"]),
                          "machine_key": fp.export_key(self.machines_key(), secret=True) if encrypted else None}
            except RuntimeError as e:
                message(self, Gtk.MessageType.ERROR, "Couldn't export the keys", GLib.markup_escape_text(str(e)))
                return
            path = choose_file(self, "Save the Enrollment File", Gtk.FileChooserAction.SAVE,
                               "firox.firoxenroll", [("Firox enrollment files", "*.firoxenroll")])
            if not path:
                return
            fp.write_json(path, bundle, 0o600)
            note = ("It holds the key that decrypts your policies: copy it only to the computers you "
                    "enroll, then delete the copies. " if encrypted else "")
            message(self, Gtk.MessageType.INFO, "Enrollment file saved",
                    f"{note}On each computer, run:\n\n<tt>sudo firox-policy enroll "
                    f"{GLib.markup_escape_text(os.path.basename(path))}</tt>")

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.firox.PolicyEditor")

        def do_activate(self):
            win = self.get_active_window() or Editor(self)
            win.show_all()
            win.present()

    import sys
    return App().run([sys.argv[0]])
