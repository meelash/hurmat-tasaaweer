"""Open a plain GTK window showing an image (for headless tests).
usage: testwin.py IMAGE [--maximized]"""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title="testwin")
    win.set_default_size(900, 600)
    pic = Gtk.Picture.new_for_filename(sys.argv[1])
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    win.set_child(pic)
    if "--maximized" in sys.argv:
        win.maximize()
    win.present()


app = Gtk.Application(application_id="org.hurmat.TestWin", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
