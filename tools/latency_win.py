"""Test window that alternates between blank and IMAGE every PERIOD seconds,
printing 'SHOW <monotonic>' / 'HIDE <monotonic>' when each change is
presented. usage: latency_win.py IMAGE [PERIOD]"""
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

PERIOD = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title="latency")
    win.set_default_size(900, 600)
    stack = Gtk.Stack()
    stack.add_named(Gtk.Box(), "blank")
    pic = Gtk.Picture.new_for_filename(sys.argv[1])
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    stack.add_named(pic, "image")
    win.set_child(stack)
    win.present()
    state = {"show": False}

    def toggle():
        state["show"] = not state["show"]
        stack.set_visible_child_name("image" if state["show"] else "blank")
        label = "SHOW" if state["show"] else "HIDE"
        # Report the time the change is actually presented, not when it was requested.
        clock = win.get_frame_clock()
        hid = None

        def after_paint(c):
            nonlocal hid
            print(f"{label} {time.monotonic():.4f}", flush=True)
            c.disconnect(hid)

        hid = clock.connect("after-paint", after_paint)
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(int(PERIOD * 1000), toggle)


app = Gtk.Application(application_id="org.hurmat.Latency", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
