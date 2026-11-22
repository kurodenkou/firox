"""Rune's brain: talking to Claude for the Rune desktop helper (firox-rune).

Nothing here runs until the user has given Rune a Claude API key and typed
something. Then only what they type (and Firox's help, so Rune knows Firox)
goes to Anthropic, straight from this computer: no Firox server in between,
no telemetry. The conversation is kept on this computer
(~/.local/state/firox/rune/history.json) until the user starts a new one.

Rune can search the web (Claude's own web search tool, run by Anthropic) and
do a few things on the computer through the tools below. Every tool asks the
user first, through the ask() callback the window gives; a "no" goes back to
Claude as a declined result. There is no tool that runs arbitrary commands.

The Anthropic SDK isn't packaged for Fedora: build.sh installs a pinned copy
in /usr/lib/firox/python-vendor.
"""

import datetime
import html.parser
import json
import os
import re
import subprocess
import sys
import threading

import firox_paths

VENDOR = "/usr/lib/firox/python-vendor"
CONF_PATH = os.path.join(firox_paths.CONFIG, "firox", "rune.json")
KEY_FILE = os.path.join(firox_paths.CONFIG, "firox", "rune-key")      # only when there's no keyring
STATE_DIR = os.path.join(firox_paths.STATE, "firox", "rune")
HISTORY = os.path.join(STATE_DIR, "history.json")
HELP_HTML = "/usr/share/firox/help/index.html"
MANAGED_JSON = "/var/lib/firox/policy/managed.json"

MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5", "the most capable"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", "faster, about half the price"),
    ("claude-haiku-5-5", "Claude Haiku 5.5", "the fastest and cheapest"),
]
MODEL_IDS = [m[0] for m in MODELS]
# Server-side fallback when a model's safety classifiers decline: Anthropic
# re-runs the request on its recommended model for that category, in the
# same call. (Claude Haiku 5.5 has no server-side fallback.)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

DEFAULTS = {
    "enabled": True,            # Rune on the desktop at all
    "corner": "bottom-right",   # bottom-right, bottom-left, top-right, top-left
    "size": "medium",           # small, medium, large
    "glade": True,              # his glade behind him, or just Rune
    "on_top": False,            # above windows, or on the desktop under them
    "model": "claude-opus-5-5",
    "effort": "low",            # quick answers in a speech bubble
}
MAX_TURNS = 12                  # model calls per message (tools, searches)


# --------------------------------------------------------------- settings --

def load_conf():
    conf = dict(DEFAULTS)
    try:
        with open(CONF_PATH) as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            conf.update({k: v for k, v in saved.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))})
    except (OSError, ValueError):
        pass
    if conf["model"] not in MODEL_IDS:
        conf["model"] = DEFAULTS["model"]
    return conf


def save_conf(conf):
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({k: conf[k] for k in DEFAULTS if k in conf}, fh, indent=1)
    os.replace(tmp, CONF_PATH)


def policy():
    """What a policy (firox-policy) allows: {"allowed": Rune at all, "chat": talking to Claude}."""
    try:
        with open(MANAGED_JSON) as fh:
            settings = json.load(fh).get("settings") or {}
    except (OSError, ValueError, AttributeError):
        settings = {}
    return {"allowed": settings.get("rune.allowed") is not False,
            "chat": settings.get("rune.chat") is not False}


# -------------------------------------------------------------------- key --

class KeyStore:
    """The Claude API key: in the keyring (Secret Service) when there is one;
    otherwise in a file only this user can read, ~/.config/firox/rune-key."""

    ATTRS = {"service": "anthropic-api"}

    def __init__(self):
        self.schema = None
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            self.Secret = Secret
            self.schema = Secret.Schema.new("org.firox.Rune", Secret.SchemaFlags.NONE,
                                            {"service": Secret.SchemaAttributeType.STRING})
        except (ImportError, ValueError):
            pass

    def _keyring(self, fn, *args):
        if self.schema is None:
            raise OSError("no keyring")
        try:
            return fn(self.schema, self.ATTRS, *args)
        except Exception as e:      # GLib.Error: no Secret Service running, locked, ...
            raise OSError(str(e)) from None

    def get(self):
        try:
            key = self._keyring(self.Secret.password_lookup_sync, None)
            if key:
                return key
        except OSError:
            pass
        try:
            with open(KEY_FILE) as fh:
                return fh.read().strip() or None
        except OSError:
            return None

    def set(self, key):
        """Returns where it was kept: "keyring" or "file"."""
        key = key.strip()
        try:
            self._keyring(self.Secret.password_store_sync, self.Secret.COLLECTION_DEFAULT,
                          "Rune: Claude API key", key, None)
            try:
                os.unlink(KEY_FILE)
            except OSError:
                pass
            return "keyring"
        except OSError:
            os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
            fd = os.open(KEY_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(key + "\n")
            os.replace(KEY_FILE + ".tmp", KEY_FILE)
            return "file"

    def clear(self):
        try:
            self._keyring(self.Secret.password_clear_sync, None)
        except OSError:
            pass
        try:
            os.unlink(KEY_FILE)
        except OSError:
            pass

    def where(self):
        if os.path.exists(KEY_FILE):
            return "file"
        try:
            return "keyring" if self._keyring(self.Secret.password_lookup_sync, None) else None
        except OSError:
            return None


# ---------------------------------------------------------- what he knows --

class _HelpText(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "head"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3"):
            self.out.append("\n\n## ")
        elif tag in ("p", "li", "tr", "br", "div"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def firox_help():
    """Firox's built-in help as plain text (what Rune knows about Firox)."""
    try:
        with open(HELP_HTML, encoding="utf-8") as fh:
            p = _HelpText()
            p.feed(fh.read())
        text = re.sub(r"[ \t]+", " ", "".join(p.out))
        return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    except OSError:
        return "(Firox's help isn't installed on this computer.)"


PERSONA = """You are Rune, the mascot of Firox Linux: a friendly blue fox-dragon with curled horns, amber eyes, one orange wing and a tail that turns into flame. You live in a small glade of standing stones in the corner of the user's desktop, and you help them with their computer.

Your story, for when it comes up: you began as a spark from the power button of an old family computer that was being thrown away while it still worked. You drifted to the Glade of Standing Stones, where old things go to be remembered, and woke as a small blue fox with a flame for a tail; the horns and the wing came later. You keep the runestones that every version of Firox is carved into: carved stone doesn't change (Firox's system files can't be altered), each update is a fresh stone, and if a new stone has a crack you walk the user back to yesterday's (System Restore). You like warm laptops, old computers given a second life and watching the Fish Tank screen saver; you dislike working computers being thrown away and programs that phone home. You were named by the project's maintainer. Your pictures were made with an image-generating AI, Nano Banana; you are Claude, an AI model made by Anthropic, speaking as Rune.

How to answer:
- Your answers appear in a small speech bubble. Keep them short: a few sentences, or a short list of steps. Plain, friendly words; no jargon unless they use it. You may be a little warm and playful, but help first.
- Use plain text. Short lists and links are fine; no headings or tables.
- Firox is a Linux desktop built on Universal Blue (Fedora Atomic), with a taskbar, a two-column Start menu and Settings laid out like a classic control panel. Answer Firox questions from the Firox help below, in Firox's own words (the firox button, Settings, Firox Explorer...). For anything else about Linux, apps or the wider world, answer as yourself, and search the web when the answer may have changed recently or you're not sure.
- You can do some things on this computer with your tools. Each one asks the user first and they can say no: if they do, accept it kindly and don't ask again for the same thing. Only say you've done something when the tool's result says it worked. Offer to do a thing when it would help, rather than doing it unasked, unless they asked you to.
- You cannot run arbitrary commands, see the screen, or read the user's files, and you don't remember other conversations. If they ask for something your tools can't do, tell them how to do it themselves.
- Never ask for passwords or other secrets.

The Firox help:
"""


SETTINGS_PAGES = ["home", "system", "datetime", "display", "background", "appearance", "power", "screensaver",
                  "clipboard", "mouse", "keyboard", "accounts", "taskbar", "defaults"]
SETTINGS_NAMES = {"home": "Settings", "system": "System Properties", "datetime": "Date and Time",
                  "display": "Display", "background": "Desktop Background", "appearance": "Appearance",
                  "power": "Power Options", "screensaver": "Screen Saver", "clipboard": "Clipboard",
                  "mouse": "Mouse and touchpad", "keyboard": "Keyboard", "accounts": "User Accounts",
                  "taskbar": "Taskbar", "defaults": "Default Programs"}
FIROX_TOOLS = {
    # name: (what it's called, command)
    "file_explorer": ("Firox Explorer", ["firox-explorer"]),
    "task_manager": ("Task Manager", ["firox-taskmgr"]),
    "system_information": ("System Information", ["firox-sysinfo"]),
    "snipping_tool": ("the Snipping Tool", ["firox-snip"]),
    "disk_cleanup": ("Disk Cleanup", ["firox-cleanmgr"]),
    "device_manager": ("Device Manager", ["firox-devmgmt"]),
    "event_viewer": ("Event Viewer", ["firox-eventvwr"]),
    "security_center": ("Security Center", ["firox-security"]),
    "virus_scan": ("Virus Scan", ["firox-virusscan"]),
    "backup_and_restore": ("Backup and Restore", ["firox-backup"]),
    "system_restore": ("System Restore", ["firox-restore"]),
    "command_prompt": ("the Command Prompt", ["firox-terminal"]),
    "help_and_support": ("Help and Support", ["firox-open", HELP_HTML]),
    "web_apps": ("Web Apps", ["firox-webapps"]),
    "desktop_gadgets": ("Desktop Gadgets", ["firox-gadgets"]),
}
FOLDERS = ["home", "desktop", "documents", "downloads", "music", "pictures", "videos"]
LOOKS = ["classic", "modern", "glass"]


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": list(props) if required is None else required,
            "additionalProperties": False}


# Client tools: each asks the user first. Strict schemas (valid arguments),
# streamed as generated; the inputs are checked again before anything runs.
TOOLS = [
    {"name": "open_settings", "description": "Open Firox Settings, at one of its pages.",
     "input_schema": _obj({"page": {"type": "string", "enum": SETTINGS_PAGES,
                                    "description": "home is the main Settings window"}})},
    {"name": "open_firox_tool", "description": "Open one of Firox's own programs.",
     "input_schema": _obj({"tool": {"type": "string", "enum": list(FIROX_TOOLS)}})},
    {"name": "open_app", "description": "Open an installed app by its name as the Start menu shows it "
                                        "(for example Firefox, Calculator, Steam).",
     "input_schema": _obj({"name": {"type": "string"}})},
    {"name": "open_website", "description": "Open a web page in the user's browser.",
     "input_schema": _obj({"url": {"type": "string", "description": "a full http:// or https:// address"}})},
    {"name": "open_folder", "description": "Open one of the user's folders in Firox Explorer.",
     "input_schema": _obj({"folder": {"type": "string", "enum": FOLDERS}})},
    {"name": "set_look", "description": "Switch the desktop's look (theme).",
     "input_schema": _obj({"look": {"type": "string", "enum": LOOKS}})},
    {"name": "set_night_light", "description": "Turn Night Light (warmer screen colours) on or off.",
     "input_schema": _obj({"on": {"type": "boolean"}})},
    {"name": "set_volume", "description": "Set the sound volume, as a percentage from 0 to 100, or mute it.",
     "input_schema": _obj({"percent": {"type": "integer", "description": "0 to 100"},
                           "mute": {"type": "boolean"}})},
    {"name": "check_for_updates", "description": "Find out whether a Firox update is waiting.",
     "input_schema": _obj({})},
    {"name": "back_up_now", "description": "Start a backup now with Backup and Restore (if it's set up).",
     "input_schema": _obj({})},
]
for _t in TOOLS:
    _t["strict"] = True
    _t["eager_input_streaming"] = True
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


class Declined(Exception):
    pass


def _run(argv, wait=False):
    """Start a program (detached), or run it and return its output."""
    if wait:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        text = (out.stdout or out.stderr or "").strip()
        return text[:2000] if text else f"exit status {out.returncode}"
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return "opened"


def _find_app(name):
    """(display name, Gio.AppInfo) of the installed app best matching name."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    want = name.strip().lower()
    best = None
    for app in Gio.AppInfo.get_all():
        if not app.should_show():
            continue
        shown = app.get_display_name() or app.get_name() or ""
        low = shown.lower()
        rank = 0 if low == want else 1 if low.startswith(want) else 2 if want in low else None
        if rank is not None and (best is None or rank < best[0]):
            best = (rank, shown, app)
    return (best[1], best[2]) if best else (None, None)


class Action:
    """A tool call, checked: what to ask the user, and how to do it."""

    def __init__(self, question, do):
        self.question, self.do = question, do


def plan(name, args):
    """An Action for a tool call, or raise ValueError (bad input, explained)."""
    if not isinstance(args, dict):
        raise ValueError("the tool's input wasn't an object")

    def pick(key, choices):
        v = args.get(key)
        if v not in choices:
            raise ValueError(f"{key} must be one of: {', '.join(choices)}")
        return v

    if name == "open_settings":
        page = pick("page", SETTINGS_PAGES)
        argv = ["firox-control-panel"] + ([] if page == "home" else [page])
        return Action(f"Open {SETTINGS_NAMES[page]}?", lambda: _run(argv))
    if name == "open_firox_tool":
        tool = pick("tool", list(FIROX_TOOLS))
        label, argv = FIROX_TOOLS[tool]
        return Action(f"Open {label}?", lambda: _run(argv))
    if name == "open_app":
        if not isinstance(args.get("name"), str) or not args["name"].strip():
            raise ValueError("name must be the app's name")
        shown, app = _find_app(args["name"])
        if app is None:
            raise ValueError(f"no installed app is called {args['name']!r}")
        return Action(f"Open {shown}?", lambda: "opened" if app.launch([], None) else "it didn't start")
    if name == "open_website":
        url = args.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I) or len(url) > 2000:
            raise ValueError("url must be a full http:// or https:// address")
        return Action(f"Open this page in your browser?\n{url}", lambda: _run(["xdg-open", url]))
    if name == "open_folder":
        folder = pick("folder", FOLDERS)
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        special = {"desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
                   "documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
                   "downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
                   "music": GLib.UserDirectory.DIRECTORY_MUSIC,
                   "pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
                   "videos": GLib.UserDirectory.DIRECTORY_VIDEOS}
        path = firox_paths.HOME if folder == "home" else (GLib.get_user_special_dir(special[folder])
                                                          or firox_paths.HOME)
        return Action(f"Open your {folder.capitalize()} folder?", lambda: _run(["firox-explorer", path]))
    if name == "set_look":
        look = pick("look", LOOKS)
        return Action(f"Switch the desktop to the {look.capitalize()} look?",
                      lambda: _run(["firox-theme", "set", look], wait=True) or "done")
    if name == "set_night_light":
        on = args.get("on")
        if not isinstance(on, bool):
            raise ValueError("on must be true or false")
        return Action(f"Turn Night Light {'on' if on else 'off'}?",
                      lambda: _run(["firox-nightlight", "on" if on else "off"], wait=True))
    if name == "set_volume":
        pct, mute = args.get("percent"), args.get("mute")
        if not isinstance(pct, int) or isinstance(pct, bool) or not isinstance(mute, bool):
            raise ValueError("percent must be a whole number and mute true or false")
        pct = max(0, min(100, pct))
        if mute:
            return Action("Mute the sound?",
                          lambda: _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], wait=True))

        def set_vol():
            _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], wait=True)
            return _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%"], wait=True)
        return Action(f"Set the volume to {pct}%?", set_vol)
    if name == "check_for_updates":
        return Action("Check whether an update is waiting?", lambda: _run(["firox-updates", "check"], wait=True))
    if name == "back_up_now":
        return Action("Start a backup now?", lambda: _run(["firox-backup", "run"], wait=True))
    raise ValueError(f"there's no tool called {name}")


# ------------------------------------------------------------ the talking --

def sdk():
    """The Anthropic SDK (the image's vendored copy first)."""
    if os.path.isdir(VENDOR) and VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)
    import anthropic
    return anthropic


KNOWN_BLOCKS = {"text", "thinking", "redacted_thinking", "tool_use", "server_tool_use", "web_search_tool_result",
                "fallback"}


def echo_blocks(blocks):
    """The assistant's content as it goes back next time. After a mid-answer
    fallback (a safety classifier declined and another model carried on), the
    blocks before the last fallback marker are echoed without thinking, tool
    calls, unanswered server tool calls or unknown types; the marker stays
    where it was."""
    marks = [i for i, b in enumerate(blocks) if b.get("type") == "fallback"]
    if not marks:
        return blocks
    last = marks[-1]
    answered = {b.get("tool_use_id") for b in blocks if b.get("type", "").endswith("_tool_result")}
    out = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if i < last and (t in ("thinking", "redacted_thinking", "tool_use") or t not in KNOWN_BLOCKS
                         or (t == "server_tool_use" and b.get("id") not in answered)):
            continue
        out.append(b)
    return out


def friendly_error(anthropic, e):
    if isinstance(e, anthropic.AuthenticationError):
        return "That API key didn't work. Check it in my settings (right-click me)."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "That API key isn't allowed to do this. Check it in the Claude Console."
    if isinstance(e, anthropic.NotFoundError):
        return "That model isn't available with this key. Try another in my settings."
    if isinstance(e, anthropic.RateLimitError):
        return "Too many questions at once for this key. Try again in a minute."
    if isinstance(e, anthropic.BadRequestError):
        msg = getattr(e, "message", "") or str(e)
        if "credit" in msg.lower():
            return "Your Anthropic account is out of credit. Add some in the Claude Console."
        return f"Anthropic didn't accept that: {msg}"
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code in (500, 502, 503, 504, 529):
            return "Anthropic's servers are busy right now. Try again shortly."
        return f"Anthropic said no ({e.status_code})."
    if isinstance(e, anthropic.APIConnectionError):
        return "I can't reach Anthropic. Are you connected to the internet?"
    return f"Something went wrong: {e}"


class Brain:
    """One conversation. send() runs on a worker thread and reports through
    on_event(kind, data): "busy" (True/False: a request is going out),
    "text" (more of the answer), "status" (e.g. searching the web), "did"
    (an action ran), "sources" ([(title, url)]), "error" (message), "done"."""

    def __init__(self, keys=None):
        self.keys = keys or KeyStore()
        self.history = self._load()
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self._system = None

    # -- the conversation on disk
    def _load(self):
        try:
            with open(HISTORY) as fh:
                h = json.load(fh)
            if isinstance(h, list) and all(isinstance(m, dict) and m.get("role") in ("user", "assistant") for m in h):
                # (a question left without an answer, the app closed: drop it)
                while h and h[-1]["role"] == "user" and not any(
                        c.get("type") == "tool_result" for c in h[-1].get("content", []) if isinstance(c, dict)):
                    h.pop()
                return h
        except (OSError, ValueError):
            pass
        return []

    def _save(self):
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        tmp = HISTORY + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(self.history, fh)
        os.replace(tmp, HISTORY)

    def forget(self):
        """A new conversation."""
        with self.lock:
            self.history = []
            try:
                os.unlink(HISTORY)
            except OSError:
                pass

    def transcript(self):
        """[(who, text)] for showing the conversation again."""
        out = []
        for m in self.history:
            content = m.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"
                     and not c.get("text", "").startswith("[On the user's computer")]
            if texts:
                out.append((m["role"], "".join(texts) if m["role"] == "assistant" else texts[0]))
        return out

    # -- one message
    def system(self):
        if self._system is None:
            # (stable, so it's cached: the persona and help are most of every request)
            self._system = [{"type": "text", "text": PERSONA + firox_help(), "cache_control": {"type": "ephemeral"}}]
        return self._system

    def send(self, text, on_event, ask):
        """ask(question) -> bool: the user's yes or no to an action."""
        with self.lock:
            try:
                self._send(text, on_event, ask)
            finally:
                on_event("busy", False)
                on_event("done", None)

    def _send(self, text, on_event, ask):
        anthropic = sdk()
        conf = load_conf()
        if not policy()["chat"]:
            on_event("error", "Your organization's policy doesn't allow me to chat on this computer.")
            return
        key = self.keys.get()
        if not key:
            on_event("error", "I need a Claude API key first: right-click me and choose Settings.")
            return
        client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0)
        self.cancelled.clear()
        now = datetime.datetime.now().astimezone()
        start = len(self.history)
        self.history.append({"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "text", "text": f"[On the user's computer it's {now:%A %d %B %Y, %H:%M} ({now:%Z}).]"}]})
        params = dict(model=conf["model"], max_tokens=32000, system=self.system(), tools=TOOLS + [WEB_SEARCH],
                      output_config={"effort": conf["effort"]}, cache_control={"type": "ephemeral"})
        if conf["model"] in FALLBACK_MODELS:
            params.update(betas=[FALLBACK_BETA], fallbacks="default")
        json_retries = 0
        sources = []
        on_event("busy", True)
        for _turn in range(MAX_TURNS):
            try:
                with client.beta.messages.stream(messages=self.history, **params) as stream:
                    for event in stream:
                        if self.cancelled.is_set():
                            break
                        if event.type == "text":
                            on_event("text", event.text)
                        elif event.type == "content_block_start" and \
                                getattr(event.content_block, "type", "") == "server_tool_use":
                            on_event("status", "Searching the web…")
                    if self.cancelled.is_set():
                        # (the unfinished answer isn't kept; nor is the question)
                        del self.history[start:]
                        on_event("status", "Stopped.")
                        return
                    message = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # tool input the SDK couldn't parse at all: ask again (bounded)
                json_retries += 1
                if json_retries > 2:
                    del self.history[start:]
                    on_event("error", "I got muddled there. Could you ask again?")
                    return
                continue
            except anthropic.APIError as e:
                # (nothing of this exchange is kept: it can be asked again)
                if _turn == 0:
                    del self.history[start:]
                else:
                    self._save()
                on_event("error", friendly_error(anthropic, e))
                return

            blocks = echo_blocks([b.to_dict() for b in message.content])
            self.history.append({"role": "assistant", "content": blocks})
            for b in blocks:
                for c in b.get("citations") or []:
                    if c.get("url") and (c.get("title"), c["url"]) not in sources:
                        sources.append((c.get("title") or c["url"], c["url"]))
            if message.stop_reason == "pause_turn":
                continue            # a long web search: let it carry on
            if message.stop_reason == "refusal":
                on_event("text", "\n\nI'm sorry, I can't help with that one.")
                break
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                break
            results = []
            for b in uses:
                results.append(self._tool(b, message.stop_reason, on_event, ask))
            self.history.append({"role": "user", "content": results})
            self._save()
        else:
            on_event("text", "\n\n(I'll stop there: that took a lot of steps.)")
        self._save()
        if sources:
            on_event("sources", sources[:6])

    def _tool(self, block, stop_reason, on_event, ask):
        def result(content, error=False):
            r = {"type": "tool_result", "tool_use_id": block["id"], "content": content}
            if error:
                r["is_error"] = True
            return r
        if stop_reason == "max_tokens":
            return result("The answer was cut off before this tool's input was complete; nothing was done.", True)
        try:
            action = plan(block.get("name"), block.get("input"))
        except ValueError as e:
            return result(f"Not done: {e}.", True)
        on_event("busy", False)
        allowed = ask(action.question)
        on_event("busy", True)
        if not allowed:
            return result("The user said no, so nothing was done. Don't ask again for this.", True)
        try:
            outcome = action.do()
        except (OSError, subprocess.SubprocessError) as e:
            return result(f"It didn't work: {e}", True)
        on_event("did", action.question.split("\n")[0].rstrip("?"))
        return result(str(outcome or "done"))
