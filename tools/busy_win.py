"""Test window that redraws every frame (stands in for video, scrolling or
streaming text). usage: busy_win.py [WIDTH HEIGHT]"""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

W, H = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1200, 900)


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title="busy")
    win.set_default_size(W, H)
    area = Gtk.DrawingArea()
    frame = {"n": 0}

    def draw(_area, cr, w, h):
        n = frame["n"]
        # Big colour steps so the whole window counts as changed every frame.
        cr.set_source_rgb(((n * 37) % 256) / 255, 0.3, ((n * 91) % 256) / 255)
        cr.paint()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle((n * 7) % max(1, w - 100), (n * 3) % max(1, h - 100), 100, 100)
        cr.fill()

    def tick(_widget, _clock):
        frame["n"] += 1
        area.queue_draw()
        return True

    area.set_draw_func(draw)
    area.add_tick_callback(tick)
    win.set_child(area)
    win.present()


app = Gtk.Application(application_id="org.hurmat.Busy", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
