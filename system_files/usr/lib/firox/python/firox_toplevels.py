"""
firox_toplevels: the open windows, from the compositor's
wlr-foreign-toplevel-management (labwc), for firox-panel and firox-taskmgr.

    tl = Toplevels(on_change)      # on_change() after windows change
    tl.ordered()                   # [Window] in the order they opened
    tl.activate(w) / minimize / restore / maximize / close

Raises Unavailable if there's no Wayland display or no window list.
"""

import struct
import sys

from gi.repository import GLib, Gtk
from pywayland.client import Display
try:
    from pywayland._ffi import lib as wl_lib
except ImportError:
    wl_lib = None
from pywayland.protocol.wayland import WlOutput, WlSeat
from pywayland.protocol.xdg_output_unstable_v1 import ZxdgOutputManagerV1

from firox_wlr_toplevel import ZwlrForeignToplevelManagerV1


class Unavailable(Exception):
    pass


# A window's name when it has no title: firox-panel sets this to look up the
# app's name from its .desktop file
NAME_FOR = lambda app_id: app_id or "Program"  # noqa: E731


# ------------------------------------------------------- pywayland patch --

def patch_pywayland():
    """pywayland (0.4) can't take objects the compositor creates in an event
    (a new_id argument), which is how wlr-foreign-toplevel-management hands
    out each window. Wrap those in a proxy of their interface, the way
    pywayland does for objects created by a request."""
    from pywayland import ffi, lib
    from pywayland.protocol_core.argument import ArgumentType
    from pywayland.protocol_core.message import Message

    original = Message.c_to_arguments
    if getattr(original, "firox_patched", False):
        return

    def c_to_arguments(self, args_ptr):
        if not any(a.argument_type == ArgumentType.NewId for a in self.arguments):
            return original(self, args_ptr)
        args = []
        for i, argument in enumerate(self.arguments):
            arg = args_ptr[i]
            kind = argument.argument_type
            if kind == ArgumentType.NewId:
                args.append(argument.interface.proxy_class(arg.o, DISPLAY))
            elif kind == ArgumentType.Int:
                args.append(arg.i)
            elif kind == ArgumentType.Uint:
                args.append(arg.u)
            elif kind == ArgumentType.Fixed:
                args.append(lib.wl_fixed_to_double(arg.f))
            elif kind == ArgumentType.FileDescriptor:
                args.append(arg.h)
            elif kind == ArgumentType.String:
                args.append(None if arg.s == ffi.NULL else ffi.string(arg.s).decode())
            elif kind == ArgumentType.Object:
                args.append(None if arg.o == ffi.NULL else
                            argument.interface.registry.get(ffi.cast("struct wl_proxy *", arg.o)))
            elif kind == ArgumentType.Array:
                args.append(ffi.buffer(arg.a.data, arg.a.size)[:])
            else:
                raise ValueError(f"unknown argument type {kind}")
        return args

    c_to_arguments.firox_patched = True
    Message.c_to_arguments = c_to_arguments


DISPLAY = None


# ------------------------------------------------- windows (Wayland side) --

class Window:
    def __init__(self, handle, seq):
        self.handle = handle
        self.seq = seq
        self.title = ""
        self.app_id = ""
        self.outputs = set()
        self.states = set()

    @property
    def active(self):
        return 2 in self.states

    @property
    def minimized(self):
        return 1 in self.states

    @property
    def maximized(self):
        return 0 in self.states

    @property
    def label(self):
        return self.title or NAME_FOR(self.app_id)


class Toplevels:
    """The open windows, from labwc's wlr-foreign-toplevel-management."""

    def __init__(self, on_change, on_lost=None):
        self.on_change = on_change
        self.on_lost = on_lost or Gtk.main_quit
        self.windows = {}          # handle -> Window
        self.outputs = {}          # WlOutput proxy -> {"x":, "y":, "name":}
        self.globals = {}          # registry name -> proxy
        self.seat = None
        self.manager = None
        self.xdg_outputs = None
        self.xdg = {}              # WlOutput -> its xdg_output (kept alive)
        self.seq = 0
        self.pending = False
        global DISPLAY
        try:
            patch_pywayland()
        except (ImportError, AttributeError) as err:
            raise Unavailable(f"unsupported pywayland version: {err}")
        try:
            self.display = Display()
            self.display.connect()
        except Exception as err:  # noqa: BLE001 - no Wayland display
            raise Unavailable(f"can't connect to Wayland: {err}")
        DISPLAY = self.display
        registry = self.display.get_registry()
        registry.dispatcher["global"] = self.on_global
        registry.dispatcher["global_remove"] = self.on_global_remove
        self.display.roundtrip()
        if self.manager is None:
            raise Unavailable("the compositor has no window list (wlr-foreign-toplevel-management)")
        self.display.roundtrip()
        GLib.io_add_watch(self.display.get_fd(), GLib.PRIORITY_DEFAULT,
                          GLib.IOCondition.IN | GLib.IOCondition.ERR | GLib.IOCondition.HUP, self.on_io)

    # registry
    def on_global(self, registry, name, interface, version):
        if interface == "zwlr_foreign_toplevel_manager_v1":
            self.manager = registry.bind(name, ZwlrForeignToplevelManagerV1, min(version, 3))
            self.manager.dispatcher["toplevel"] = self.on_toplevel
        elif interface == "wl_seat" and self.seat is None:
            self.seat = registry.bind(name, WlSeat, min(version, 7))
        elif interface == "zxdg_output_manager_v1":
            self.xdg_outputs = registry.bind(name, ZxdgOutputManagerV1, min(version, 3))
            for out in self.outputs:
                self.watch_position(out)
        elif interface == "wl_output":
            out = registry.bind(name, WlOutput, min(version, 4))
            self.outputs[out] = {"x": None, "y": None, "name": ""}
            self.globals[name] = out
            out.dispatcher["name"] = lambda o, n: self.outputs.get(o, {}).update(name=n)
            if self.xdg_outputs is not None:
                self.watch_position(out)

    def watch_position(self, out):
        """Where the monitor sits in the desktop (the logical position GTK
        uses too), to know which taskbar a window belongs on."""
        if out in self.xdg:
            return
        xo = self.xdg_outputs.get_xdg_output(out)
        self.xdg[out] = xo

        def position(_xo, x, y):
            if out in self.outputs:
                self.outputs[out].update(x=x, y=y)
                self.changed()
        xo.dispatcher["logical_position"] = position

    def on_global_remove(self, _registry, name):
        out = self.globals.pop(name, None)
        if out is not None:
            self.outputs.pop(out, None)
            xo = self.xdg.pop(out, None)
            if xo is not None:
                try:
                    xo.destroy()
                except Exception:  # noqa: BLE001
                    pass
            for w in self.windows.values():
                w.outputs.discard(out)
            self.changed()

    # windows
    def on_toplevel(self, _manager, handle):
        self.seq += 1
        w = Window(handle, self.seq)
        self.windows[handle] = w
        handle.dispatcher["title"] = lambda h, t: setattr(w, "title", t)
        handle.dispatcher["app_id"] = lambda h, a: setattr(w, "app_id", a)
        handle.dispatcher["output_enter"] = lambda h, o: w.outputs.add(o)
        handle.dispatcher["output_leave"] = lambda h, o: w.outputs.discard(o)

        def state(_h, states):
            if isinstance(states, (bytes, bytearray)):
                states = struct.unpack(f"{len(states) // 4}I", bytes(states[:len(states) // 4 * 4]))
            w.states = set(states)
        handle.dispatcher["state"] = state
        handle.dispatcher["done"] = lambda h: self.changed()
        handle.dispatcher["parent"] = lambda h, p: None

        def closed(h):
            self.windows.pop(h, None)
            try:
                h.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.changed()
        handle.dispatcher["closed"] = closed

    def on_io(self, _fd, cond):
        if cond & (GLib.IOCondition.ERR | GLib.IOCondition.HUP):
            print("lost the Wayland connection", file=sys.stderr)
            self.on_lost()
            return False
        try:
            self.read_and_dispatch()
        except Exception as err:  # noqa: BLE001
            print(f"Wayland error: {err}", file=sys.stderr)
            self.on_lost()
            return False
        return True

    def read_and_dispatch(self):
        """Read what the compositor sent and handle it, without ever waiting.

        display.dispatch(block=True) waits for new data whenever its queue is
        empty, even if GLib woke us for data that has already been read.
        That froze the whole taskbar (seen when a monitor went away during
        the screen saver) until the next window event. This is libwayland's
        non-blocking sequence instead: prepare, read what's there, dispatch.
        """
        d = self.display
        ptr = getattr(d, "_ptr", None)
        if ptr is None or wl_lib is None or not hasattr(wl_lib, "wl_display_prepare_read"):
            d.dispatch(block=True)          # older pywayland: the old way
            d.flush()
            return
        while wl_lib.wl_display_prepare_read(ptr) != 0:
            if wl_lib.wl_display_dispatch_pending(ptr) == -1:
                raise RuntimeError("dispatch failed")
        wl_lib.wl_display_flush(ptr)
        # The socket is non-blocking: with nothing to read this returns at once
        if wl_lib.wl_display_read_events(ptr) == -1:
            raise RuntimeError(f"read failed ({wl_lib.wl_display_get_error(ptr)})")
        if wl_lib.wl_display_dispatch_pending(ptr) == -1:
            raise RuntimeError(f"dispatch failed ({wl_lib.wl_display_get_error(ptr)})")
        wl_lib.wl_display_flush(ptr)

    def changed(self):
        if not self.pending:
            self.pending = True
            GLib.idle_add(self._emit)

    def _emit(self):
        self.pending = False
        self.on_change()
        return False

    def ordered(self):
        return sorted(self.windows.values(), key=lambda w: w.seq)

    def output_at(self, x, y):
        return [o for o, info in self.outputs.items() if (info["x"], info["y"]) == (x, y)]

    # actions
    def _do(self, fn):
        try:
            fn()
            self.display.flush()
        except Exception as err:  # noqa: BLE001 - the window went away meanwhile
            print(f"window action: {err}", file=sys.stderr)

    def activate(self, w):
        def go():
            if w.minimized:
                w.handle.unset_minimized()
            if self.seat is not None:
                w.handle.activate(self.seat)
        self._do(go)

    def minimize(self, w):
        self._do(w.handle.set_minimized)

    def restore(self, w):
        def go():
            if w.minimized:
                w.handle.unset_minimized()
            if w.maximized:
                w.handle.unset_maximized()
            if self.seat is not None:
                w.handle.activate(self.seat)
        self._do(go)

    def maximize(self, w):
        def go():
            if w.minimized:
                w.handle.unset_minimized()
            w.handle.set_maximized()
            if self.seat is not None:
                w.handle.activate(self.seat)
        self._do(go)

    def close(self, w):
        self._do(w.handle.close)
