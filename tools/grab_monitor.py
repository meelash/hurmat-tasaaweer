"""Grab one PNG from a Mutter ScreenCast stream (for headless tests).

    grab_monitor.py monitor out.png
    grab_monitor.py window:<id> out.png
"""
import sys, gi
gi.require_version('Gst','1.0')
from gi.repository import Gio, GLib, Gst
Gst.init(None)
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
def call(path, iface, method, args, fmt, ret=None):
    return bus.call_sync("org.gnome.Mutter.ScreenCast", path, iface, method, GLib.Variant(fmt, args) if fmt else None,
                         GLib.VariantType(ret) if ret else None, 0, -1, None)
sess = call("/org/gnome/Mutter/ScreenCast", "org.gnome.Mutter.ScreenCast", "CreateSession", ({"is-platform": GLib.Variant("b", True)},), "(a{sv})", "(o)").unpack()[0]
kind = sys.argv[1]
if kind == "monitor":
    stream = call(sess, "org.gnome.Mutter.ScreenCast.Session", "RecordMonitor",
                  ("", {"cursor-mode": GLib.Variant("u", 0)}), "(sa{sv})", "(o)").unpack()[0]
else:
    wid = int(kind.split(":")[1])
    stream = call(sess, "org.gnome.Mutter.ScreenCast.Session", "RecordWindow",
                  ({"window-id": GLib.Variant("t", wid), "cursor-mode": GLib.Variant("u", 0)},), "(a{sv})", "(o)").unpack()[0]
node = {}
loop = GLib.MainLoop()
def added(_c, _s, _p, _i, _sig, params): node["id"] = params.unpack()[0]; loop.quit()
bus.signal_subscribe("org.gnome.Mutter.ScreenCast", "org.gnome.Mutter.ScreenCast.Stream", "PipeWireStreamAdded", stream, None, 0, added)
call(sess, "org.gnome.Mutter.ScreenCast.Session", "Start", None, None)
GLib.timeout_add_seconds(5, loop.quit); loop.run()
p = Gst.parse_launch(f"pipewiresrc path={node['id']} num-buffers=1 ! videoconvert ! pngenc snapshot=false ! multifilesink location={sys.argv[2]}")
p.set_state(Gst.State.PLAYING)
msg = p.get_bus().timed_pop_filtered(8 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
print(kind, "->", msg.type if msg else "timeout")
p.set_state(Gst.State.NULL)
call(sess, "org.gnome.Mutter.ScreenCast.Session", "Stop", None, None)
