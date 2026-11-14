"""Launchers: a desktop (and optionally Start menu) shortcut to a program.

Used by the desktop's New > Launcher... and Firox Explorer's "Create Desktop
Shortcut". A Windows program (.exe, .msi, .bat, .cmd) gets a launcher that
runs it with Proton-GE through firox-windows, with the .exe's own icon
(extracted with wrestool and icotool from icoutils) and StartupWMClass set
to the .exe's name, so the taskbar can match its window. Start menu copies
of Windows programs go in their own "Windows Programs" folder
(Categories=X-Firox-Windows).

    path = write_launcher(folder, name, program, args="", terminal=False)
    LauncherDialog(parent, folder, program=None).run_and_create() -> [paths]
"""

import glob
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

WINDOWS_EXT = (".exe", ".msi", ".bat", ".cmd", ".com")
DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
ICONS = os.path.join(DATA, "icons", "firox-launchers")
APPS = os.path.join(DATA, "applications")


def kind_of(path):
    """'windows', 'appimage', 'script', 'program' or None (not runnable)."""
    if not path or not os.path.isfile(path):
        return None
    low = path.lower()
    if low.endswith(WINDOWS_EXT):
        return "windows"
    ct = Gio.content_type_guess(path, None)[0] or ""
    if Gio.content_type_is_a(ct, "application/x-msdownload"):
        return "windows"
    if low.endswith(".appimage") or "appimage" in ct:
        return "appimage"
    if ct in ("application/x-shellscript", "text/x-shellscript", "application/x-sh", "text/x-python",
              "text/x-python3", "application/x-python", "text/x-perl"):
        return "script"
    if Gio.content_type_can_be_executable(ct) or os.access(path, os.X_OK):
        return "program"
    return None


def default_name(path):
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if ext.lower() in WINDOWS_EXT + (".appimage", ".sh", ".py"):
        base = stem
    # "my_game-x86_64" -> "my game", but keep names that look deliberate
    nice = re.sub(r"[-_](x86_64|x64|amd64|linux|win64|win32)$", "", base, flags=re.I)
    return nice.replace("_", " ").strip() or base


def quote_exec(arg):
    """One Exec= argument, quoted per the Desktop Entry spec."""
    if arg and not re.search(r'[\s"\'\\><~|&;$*?#()`=%]', arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"' + escaped.replace("%", "%%") + '"'


def split_args(text):
    import shlex
    try:
        return shlex.split(text or "")
    except ValueError:
        return (text or "").split()


def exe_icon(path):
    """The .exe's largest icon as a PNG in ~/.local/share/icons/firox-launchers,
    or None (no icoutils, or the program has no icon)."""
    if not (shutil.which("wrestool") and shutil.which("icotool")):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:12]
    dest = os.path.join(ICONS, f"{re.sub(r'[^A-Za-z0-9]+', '-', default_name(path)).strip('-').lower()}-{key}.png")
    if os.path.exists(dest):
        return dest
    with tempfile.TemporaryDirectory() as tmp:
        try:
            # type 14 = RT_GROUP_ICON: wrestool writes each icon group as an .ico
            subprocess.run(["wrestool", "-x", "-t", "14", "-o", tmp, path], capture_output=True, timeout=30)
            for ico in glob.glob(os.path.join(tmp, "*.ico")):
                subprocess.run(["icotool", "-x", "-o", tmp, ico], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        best, best_score = None, -1
        for png in glob.glob(os.path.join(tmp, "*.png")):
            # icotool names them NAME_INDEX_WxHxBPP.png
            m = re.search(r"_(\d+)x(\d+)x(\d+)\.png$", png)
            w, bpp = (int(m.group(1)), int(m.group(3))) if m else (0, 0)
            score = w * 100 + bpp
            if score > best_score:
                best, best_score = png, score
        if best is None:
            return None
        os.makedirs(ICONS, exist_ok=True)
        shutil.copy(best, dest)
    return dest


def entry_text(name, program, args="", terminal=False, icon=None):
    kind = kind_of(program) or "program"
    argv = split_args(args)
    if kind == "windows":
        cmd = ["firox-windows", program] + argv
        icon = icon or exe_icon(program) or "wine-launcher"
    elif kind == "script" and not os.access(program, os.X_OK):
        cmd = ["/bin/sh", program] + argv
    else:
        cmd = [program] + argv
    if not icon:
        icon = "application-x-executable"
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}",
             f"Exec={' '.join(quote_exec(a) for a in cmd)}",
             f"Path={os.path.dirname(program)}", f"Icon={icon}",
             f"Terminal={'true' if terminal and kind != 'windows' else 'false'}"]
    if kind == "windows":
        lines += [f"Comment=Windows program, runs with Proton-GE ({os.path.basename(program)})",
                  f"StartupWMClass={os.path.basename(program)}", "Categories=X-Firox-Windows;"]
    else:
        lines += [f"Comment={program}"]
    lines.append("X-Firox-Launcher=true")
    return "\n".join(lines) + "\n", kind


def safe_filename(name):
    s = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", name)
    s = re.sub(r"\s+", " ", s).strip().strip(".") or "Launcher"
    return s[:120]


def unique(folder, stem, ext):
    path = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path


def set_launcher_name(path, name, rename_file=True):
    """Rename a launcher (.desktop file) the way people see it: change its
    Name= (and drop translated names, which would win over it), and by
    default give the file a matching name. A launcher that is a link to an
    app's own entry (read-only) becomes a copy of it here. Returns the
    launcher's path afterwards. Raises OSError."""
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        lines = fh.read().splitlines()
    out, group, done = [], None, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if group == "[Desktop Entry]" and not done:
                out.append(f"Name={name}")
                done = True
            group = stripped
        elif group == "[Desktop Entry]":
            key = stripped.split("=", 1)[0].strip()
            if key == "Name":
                if not done:
                    out.append(f"Name={name}")
                    done = True
                continue
            if key.startswith("Name["):
                continue
        out.append(line)
    if not done:
        if "[Desktop Entry]" not in (l.strip() for l in out):
            out.insert(0, "[Desktop Entry]")
        idx = next(i for i, l in enumerate(out) if l.strip() == "[Desktop Entry]")
        out.insert(idx + 1, f"Name={name}")
    folder = os.path.dirname(os.path.abspath(path))
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = 0o755
    if os.path.islink(path):
        mode |= 0o755
    tmp = tempfile.NamedTemporaryFile("w", dir=folder, prefix=".launcher-", suffix=".tmp",
                                      delete=False, encoding="utf-8", errors="surrogateescape")
    with tmp:
        tmp.write("\n".join(out) + "\n")
    os.chmod(tmp.name, mode)
    os.replace(tmp.name, path)              # a link is replaced by the copy, its target untouched
    if rename_file:
        current = os.path.basename(path)
        wanted = safe_filename(name) + ".desktop"
        if wanted != current:
            target = os.path.join(folder, wanted)
            if os.path.exists(target):
                target = unique(folder, safe_filename(name), ".desktop")
            os.rename(path, target)
            path = target
    return path


def write_launcher(folder, name, program, args="", terminal=False, start_menu=False):
    """Write NAME.desktop in folder (and in the Start menu if asked); returns
    the paths written."""
    text, kind = entry_text(name, program, args, terminal)
    os.makedirs(folder, exist_ok=True)
    paths = [unique(folder, safe_filename(name), ".desktop")]
    if start_menu:
        os.makedirs(APPS, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "launcher"
        paths.append(unique(APPS, f"firox-launcher-{slug}", ".desktop"))
    for path in paths:
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o755)
    # Making a launcher is saying "I trust this program": mark it runnable so
    # it doesn't ask again (firox-windows and the desktop ask for downloads)
    try:
        mode = os.stat(program).st_mode
        if not mode & 0o100:
            os.chmod(program, mode | 0o100 | ((mode & 0o044) >> 2))
    except OSError:
        pass
    return paths


# --------------------------------------------------------------------- UI --

class LauncherDialog(Gtk.Dialog):
    def __init__(self, parent, folder, program=None):
        super().__init__(title="Create Launcher", transient_for=parent, modal=True)
        self.folder = folder
        self.set_default_size(520, -1)
        self.set_icon_name("application-x-executable")
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.create = self.add_button("C_reate", Gtk.ResponseType.OK)
        self.create.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)

        head = Gtk.Box(spacing=12)
        self.preview = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        self.preview.set_pixel_size(48)
        head.pack_start(self.preview, False, False, 0)
        intro = Gtk.Label(xalign=0, wrap=True, max_width_chars=52,
                          label="Make a shortcut on the desktop to a program: a Windows program (.exe or "
                                ".msi), an AppImage, or any program or script.")
        head.pack_start(intro, True, True, 0)
        box.pack_start(head, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.program = Gtk.Entry(hexpand=True, activates_default=True,
                                 placeholder_text="Choose the program with Browse…")
        self.program.connect("changed", lambda _e: self.update())
        browse = Gtk.Button.new_with_mnemonic("_Browse…")
        browse.connect("clicked", lambda _b: self.browse())
        prow = Gtk.Box(spacing=6)
        prow.pack_start(self.program, True, True, 0)
        prow.pack_start(browse, False, False, 0)
        self.args = Gtk.Entry(activates_default=True, placeholder_text="Optional, e.g. -windowed")
        self.name = Gtk.Entry(activates_default=True)
        self.name.connect("changed", lambda _e: setattr(self, "name_edited", self.name.has_focus()
                                                        or getattr(self, "name_edited", False))
                          or self.update(name_only=True))
        self.name_edited = False
        for row, (label, widget) in enumerate((("_Program:", prow), ("_Arguments:", self.args),
                                               ("_Name:", self.name))):
            lab = Gtk.Label.new_with_mnemonic(label)
            lab.set_xalign(0)
            lab.set_mnemonic_widget(widget if isinstance(widget, Gtk.Entry) else self.program)
            grid.attach(lab, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        box.pack_start(grid, False, False, 0)

        self.kind_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        self.kind_label.get_style_context().add_class("dim-label")
        box.pack_start(self.kind_label, False, False, 0)
        self.terminal = Gtk.CheckButton.new_with_mnemonic("Run in a _terminal window")
        self.terminal.set_tooltip_text("For command-line programs and scripts, so you can see what they print")
        box.pack_start(self.terminal, False, False, 0)
        self.menu = Gtk.CheckButton.new_with_mnemonic("Also add it to the _Start menu")
        box.pack_start(self.menu, False, False, 0)
        self.show_all()
        if program:
            self.program.set_text(program)
        self.update()

    def browse(self):
        dlg = Gtk.FileChooserDialog(title="Choose a program", transient_for=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.ACCEPT)
        progs = Gtk.FileFilter()
        progs.set_name("Programs")
        for pat in ("*.exe", "*.EXE", "*.msi", "*.MSI", "*.bat", "*.cmd", "*.AppImage", "*.appimage", "*.sh"):
            progs.add_pattern(pat)
        for mime in ("application/x-executable", "application/x-pie-executable", "application/x-msdownload",
                     "application/x-shellscript", "application/vnd.appimage"):
            progs.add_mime_type(mime)
        win = Gtk.FileFilter()
        win.set_name("Windows programs (.exe, .msi)")
        for pat in ("*.exe", "*.EXE", "*.msi", "*.MSI", "*.bat", "*.cmd"):
            win.add_pattern(pat)
        allf = Gtk.FileFilter()
        allf.set_name("All files")
        allf.add_pattern("*")
        for f in (progs, win, allf):
            dlg.add_filter(f)
        cur = self.program.get_text().strip()
        start = os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur)) else \
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~")
        dlg.set_current_folder(start)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            self.program.set_text(dlg.get_filename())
        dlg.destroy()

    def update(self, name_only=False):
        path = os.path.expanduser(self.program.get_text().strip())
        kind = kind_of(path)
        if not name_only:
            if path and not self.name_edited:
                self.name.set_text(default_name(path))
            icon = None
            text = ""
            if kind == "windows":
                icon = exe_icon(path)
                text = ("A Windows program: the launcher runs it with Proton-GE (firox-windows)"
                        + (", with its own icon." if icon else "."))
            elif kind == "appimage":
                text = "An AppImage."
            elif kind == "script":
                text = "A script."
            elif kind == "program":
                text = "A Linux program."
            elif path:
                text = "That isn't a file you can run." if os.path.exists(path) else "There's no file there."
            self.kind_label.set_text(text)
            self.terminal.set_visible(kind in ("script", "program"))
            if icon:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon, 48, 48, True)
                    self.preview.set_from_pixbuf(pb)
                except GLib.Error:
                    self.preview.set_from_icon_name("wine-launcher", Gtk.IconSize.DIALOG)
            else:
                self.preview.set_from_icon_name({"windows": "wine-launcher"}.get(kind, "application-x-executable"),
                                                Gtk.IconSize.DIALOG)
            self.preview.set_pixel_size(48)
        self.create.set_sensitive(bool(kind) and bool(self.name.get_text().strip()))

    def run_and_create(self):
        """Show it; on Create, write the launcher. Returns the paths written."""
        paths = []
        while self.run() == Gtk.ResponseType.OK:
            program = os.path.expanduser(self.program.get_text().strip())
            try:
                paths = write_launcher(self.folder, self.name.get_text().strip(), program,
                                       self.args.get_text(), self.terminal.get_active() and
                                       self.terminal.get_visible(), self.menu.get_active())
                break
            except OSError as err:
                msg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                        buttons=Gtk.ButtonsType.OK, text="Couldn't make the launcher")
                msg.format_secondary_text(str(err))
                msg.run()
                msg.destroy()
        self.destroy()
        return paths
