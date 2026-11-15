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
            self.set_default_size(860, 680)
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

            # -- the settings: categories | settings in it | the one selected
            self.settings = dict(conf.get("settings") or {})
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
            paned.set_position(190)
            self.tree = Gtk.TreeStore(str, str)             # label, category path
            nodes = {}
            for pol in P.POLICIES:
                parts = pol.category.split("/")
                for depth in range(len(parts)):
                    path = "/".join(parts[:depth + 1])
                    if path not in nodes:
                        nodes[path] = self.tree.append(nodes.get("/".join(parts[:depth])), [parts[depth], path])
            self.tree.prepend(None, ["All Settings", ""])
            self.cats = Gtk.TreeView(model=self.tree, headers_visible=False)
            self.cats.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0))
            self.cats.expand_all()
            self.cats.get_selection().connect("changed", lambda *_a: self.fill_list())
            left = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
            left.add(self.cats)
            paned.pack1(left, False, False)

            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.list = Gtk.ListStore(str, str, str)       # title, state, key
            self.view = Gtk.TreeView(model=self.list)
            title_col = Gtk.TreeViewColumn("Setting", Gtk.CellRendererText(ellipsize=3), text=0)
            title_col.set_expand(True)
            self.view.append_column(title_col)
            state_col = Gtk.TreeViewColumn("State", Gtk.CellRendererText(ellipsize=3), text=1)
            state_col.set_min_width(230)
            state_col.set_max_width(320)
            self.view.append_column(state_col)
            self.view.get_selection().connect("changed", lambda *_a: self.show_setting())
            top = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
            top.set_min_content_height(110)
            top.add(self.view)
            right.pack_start(top, True, True, 0)
            self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.detail.set_margin_start(6)
            self.detail.set_margin_top(4)
            self.detail.set_margin_end(6)
            # The selected setting: scrolls rather than stretching the window
            detail_scroll = Gtk.ScrolledWindow()
            detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            detail_scroll.set_min_content_height(230)
            detail_scroll.add(self.detail)
            right.pack_start(detail_scroll, False, False, 0)
            paned.pack2(right, True, False)
            outer.add(paned)
            self.cats.get_selection().select_path(Gtk.TreePath(0))

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
            return {k: v for k, v in self.settings.items() if k in P.BY_KEY}

        def state_text(self, pol):
            value = self.settings.get(pol.key)
            return "Not configured" if value is None else pol.describe(value)

        def fill_list(self):
            model, it = self.cats.get_selection().get_selected()
            category = model[it][1] if it is not None else ""
            keep = self.selected_key()
            self.list.clear()
            for pol in P.POLICIES:
                if not category or pol.category == category or pol.category.startswith(category + "/"):
                    self.list.append([pol.title if category else f"{pol.category.replace('/', ' › ')} › {pol.title}",
                                      self.state_text(pol), pol.key])
            for i, row in enumerate(self.list):
                if row[2] == keep:
                    self.view.get_selection().select_path(Gtk.TreePath(i))
                    return
            if len(self.list):
                self.view.get_selection().select_path(Gtk.TreePath(0))

        def selected_key(self):
            model, it = self.view.get_selection().get_selected() if hasattr(self, "view") else (None, None)
            return model[it][2] if it is not None else None

        def refresh_state(self, key):
            for row in self.list:
                if row[2] == key:
                    row[1] = self.state_text(P.BY_KEY[key])

        def show_setting(self):
            """The selected setting: Not configured, or its value."""
            for child in self.detail.get_children():
                self.detail.remove(child)
            key = self.selected_key()
            if key is None:
                return
            pol = P.BY_KEY[key]
            head = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
            head.set_markup(f"<b>{GLib.markup_escape_text(pol.title)}</b>\n"
                            f"<small>{GLib.markup_escape_text(pol.description)}</small>")
            self.detail.add(head)
            value = self.settings.get(key)

            def set_value(v):
                if v is None:
                    self.settings.pop(key, None)
                else:
                    self.settings[key] = v
                self.refresh_state(key)

            off = Gtk.RadioButton.new_with_label(None, "Not configured (left to each person)")
            self.detail.add(off)
            if pol.kind == "choice":
                for v, label in pol.choices:
                    b = Gtk.RadioButton.new_with_label_from_widget(off, label)
                    b.set_active(value == v)
                    b.connect("toggled", lambda b, v=v: b.get_active() and set_value(v))
                    self.detail.add(b)
                if value is None:
                    off.set_active(True)
            elif pol.kind == "bool":
                on_b = Gtk.RadioButton.new_with_label_from_widget(off, pol.on_label)
                off_b = Gtk.RadioButton.new_with_label_from_widget(off, pol.off_label)
                self.detail.add(on_b)
                self.detail.add(off_b)
                (on_b if value is True else off_b if value is False else off).set_active(True)
                on_b.connect("toggled", lambda b: b.get_active() and set_value(True))
                off_b.connect("toggled", lambda b: b.get_active() and set_value(False))
            elif pol.kind == "notice":
                self.notice_editor(pol, value, off, set_value)
            elif pol.kind == "records":
                self.records_editor(pol, value, off, set_value)
            else:
                row = Gtk.Box(spacing=6)
                set_b = Gtk.RadioButton.new_with_label_from_widget(off, "Set to")
                row.add(set_b)
                if pol.kind in ("minutes", "number"):
                    field = Gtk.SpinButton.new_with_range(pol.low, pol.high, 1)
                    field.set_value(value if isinstance(value, int) else min(max(10, pol.low), pol.high))
                    current = lambda: int(field.get_value())       # noqa: E731
                    field.connect("value-changed", lambda *_a: set_b.get_active() and set_value(current()))
                    row.add(field)
                    if pol.unit:
                        row.add(Gtk.Label(label=pol.unit))
                else:
                    shown = ", ".join(value) if isinstance(value, list) else (value or "")
                    field = Gtk.Entry(hexpand=True, text=shown, placeholder_text=pol.placeholder)
                    note = Gtk.Label(xalign=0)
                    note.get_style_context().add_class("dim-label")

                    def current():
                        text = field.get_text().strip()
                        if pol.kind == "addresses":
                            text = [a.strip() for a in text.replace(" ", ",").split(",") if a.strip()]
                        try:
                            pol.check(text)
                            note.set_text("")
                            return text
                        except P.PolicyError as e:
                            note.set_text(str(e).replace(pol.key, "It"))
                            return None

                    def typed(*_a):
                        if set_b.get_active():
                            v = current()
                            if v is not None:
                                set_value(v)
                            else:
                                set_value(None)
                                self.refresh_state(key)
                    field.connect("changed", typed)
                    row.add(field)
                self.detail.add(row)
                if pol.kind not in ("bool", "minutes", "number"):
                    self.detail.add(note)
                set_b.set_active(value is not None)
                field.set_sensitive(value is not None)

                def chosen(b):
                    field.set_sensitive(b.get_active())
                    if b.get_active():
                        v = current()
                        set_value(v)
                        if pol.kind not in ("bool", "minutes", "number"):
                            field.grab_focus()
                set_b.connect("toggled", chosen)
            off.connect("toggled", lambda b: b.get_active() and set_value(None))
            self.detail.show_all()

        def notice_editor(self, pol, value, off, set_value):
            """A login banner: a title and a message."""
            set_b = Gtk.RadioButton.new_with_label_from_widget(off, "Show this notice")
            self.detail.add(set_b)
            grid = Gtk.Grid(row_spacing=6, column_spacing=8, margin_start=24)
            grid.attach(Gtk.Label(label="Title:", xalign=1), 0, 0, 1, 1)
            title = Gtk.Entry(hexpand=True, text=(value or {}).get("title", ""),
                              placeholder_text="Authorized use only")
            grid.attach(title, 1, 0, 1, 1)
            grid.attach(Gtk.Label(label="Message:", xalign=1, valign=Gtk.Align.START), 0, 1, 1, 1)
            text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
            text.get_buffer().set_text((value or {}).get("text", ""))
            scroll = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, hexpand=True)
            scroll.set_min_content_height(90)
            scroll.add(text)
            grid.attach(scroll, 1, 1, 1, 1)
            self.detail.add(grid)

            def current():
                buf = text.get_buffer()
                body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
                return {"title": title.get_text().strip(), "text": body} if body else None

            def changed(*_a):
                if set_b.get_active():
                    set_value(current())
            title.connect("changed", changed)
            text.get_buffer().connect("changed", changed)
            set_b.set_active(value is not None)
            grid.set_sensitive(value is not None)

            def chosen(b):
                grid.set_sensitive(b.get_active())
                if b.get_active():
                    set_value(current())
                    text.grab_focus()
            set_b.connect("toggled", chosen)

        def records_editor(self, pol, value, off, set_value):
            """A list (Wi-Fi networks, printers, bookmarks) with Add/Edit/Remove."""
            records = [dict(r) for r in (value or [])]
            set_b = Gtk.RadioButton.new_with_label_from_widget(off, f"Use this list of {pol.noun[1]}")
            self.detail.add(set_b)
            store = Gtk.ListStore(str)
            view = Gtk.TreeView(model=store, headers_visible=False)
            view.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(ellipsize=3), text=0))
            scroll = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, hexpand=True)
            scroll.set_min_content_height(80)
            scroll.add(view)
            buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box = Gtk.Box(spacing=6, margin_start=24)
            box.pack_start(scroll, True, True, 0)
            box.pack_start(buttons, False, False, 0)
            self.detail.add(box)

            def summary(rec):
                parts = []
                for f in pol.fields[:2]:
                    v = rec.get(f.key)
                    if f.kind == "choice":
                        v = dict(f.choices).get(v, v)
                    if v not in (None, "") and f.kind != "password":
                        parts.append(str(v))
                return " \u2014 ".join(parts)

            def refill():
                store.clear()
                for rec in records:
                    store.append([summary(rec)])

            def changed():
                refill()
                if set_b.get_active():
                    set_value([dict(r) for r in records])

            def selected():
                _m, it = view.get_selection().get_selected()
                return store.get_path(it).get_indices()[0] if it is not None else None

            def add(_b):
                rec = self.record_dialog(pol, {})
                if rec is not None:
                    records.append(rec)
                    changed()

            def edit(*_a):
                i = selected()
                if i is not None:
                    rec = self.record_dialog(pol, records[i])
                    if rec is not None:
                        records[i] = rec
                        changed()

            def delete(_b):
                i = selected()
                if i is not None:
                    del records[i]
                    changed()

            for label, handler in (("Add\u2026", add), ("Edit\u2026", edit), ("Remove", delete)):
                b = Gtk.Button(label=label)
                b.connect("clicked", handler)
                buttons.pack_start(b, False, False, 0)
            view.connect("row-activated", edit)
            refill()
            set_b.set_active(value is not None)
            box.set_sensitive(value is not None)

            def chosen(b):
                box.set_sensitive(b.get_active())
                if b.get_active():
                    set_value([dict(r) for r in records])
            set_b.connect("toggled", chosen)

        def record_dialog(self, pol, rec):
            """Add or edit one entry: its fields, checked. None if cancelled."""
            dlg = Gtk.Dialog(title=pol.title, transient_for=self, modal=True)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.OK)
            area = dlg.get_content_area()
            margins(area)
            grid = Gtk.Grid(row_spacing=8, column_spacing=10)
            area.add(grid)
            widgets = {}
            rows = {}
            for i, f in enumerate(pol.fields):
                v = rec.get(f.key)
                if f.kind == "bool":
                    w = Gtk.CheckButton(label=f.label, active=bool(v))
                    grid.attach(w, 1, i, 1, 1)
                    label = None
                else:
                    label = Gtk.Label(label=f.label + ":", xalign=1)
                    grid.attach(label, 0, i, 1, 1)
                    if f.kind == "choice":
                        w = Gtk.ComboBoxText()
                        for c, l in f.choices:
                            w.append(c, l)
                        w.set_active_id(v or f.choices[0][0])
                    else:
                        w = Gtk.Entry(text=v or "", hexpand=True, width_chars=34, activates_default=True,
                                      placeholder_text=f.placeholder, visibility=f.kind != "password")
                    grid.attach(w, 1, i, 1, 1)
                widgets[f.key] = w
                rows[f.key] = (label, w)
            note = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
            note.get_style_context().add_class("dim-label")
            area.add(note)

            def read():
                out = {}
                for f in pol.fields:
                    w = widgets[f.key]
                    if f.kind == "bool":
                        out[f.key] = w.get_active()
                    elif f.kind == "choice":
                        out[f.key] = w.get_active_id()
                    else:
                        out[f.key] = w.get_text().strip() if f.kind != "password" else w.get_text()
                # Fields that don't apply (e.g. a username for a WPA2 Personal network) are left out
                return {k: v for k, v in out.items()
                        if next(f for f in pol.fields if f.key == k).used(out) and v not in ("", False)}

            def visibility(*_a):
                current = {f.key: (widgets[f.key].get_active_id() if f.kind == "choice" else None)
                           for f in pol.fields}
                for f in pol.fields:
                    show = f.used(current)
                    for w in rows[f.key]:
                        if w is not None:
                            w.set_visible(show)
            for f in pol.fields:
                if f.kind == "choice":
                    widgets[f.key].connect("changed", visibility)
            area.show_all()
            visibility()
            result = None
            while dlg.run() == Gtk.ResponseType.OK:
                candidate = read()
                err = pol.record_error(candidate)
                if not err:
                    result = candidate
                    break
                note.set_text(err[:1].upper() + err[1:] + ".")
            dlg.destroy()
            return result

        def load(self, settings, organization=None):
            if organization is not None:
                self.org.set_text(organization)
            self.settings = {k: v for k, v in settings.items() if k in P.BY_KEY}
            self.fill_list()

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
            secrets_in = any(r.get("password") for r in settings.get("network.wifi") or [])
            if secrets_in and not self.encrypt.get_active():
                dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                        buttons=Gtk.ButtonsType.NONE,
                                        text="This policy holds Wi-Fi passwords")
                dlg.format_secondary_text("Anyone who can fetch the policy file could read them. Encrypt it so "
                                          "only your enrolled computers can (they need an enrollment file "
                                          "made after turning encryption on).")
                dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Sign Without Encrypting", 2,
                                "Encrypt", Gtk.ResponseType.OK)
                dlg.set_default_response(Gtk.ResponseType.OK)
                answer = dlg.run()
                dlg.destroy()
                if answer == Gtk.ResponseType.OK:
                    self.encrypt.set_active(True)
                elif answer != 2:
                    return
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
