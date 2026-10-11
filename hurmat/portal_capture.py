"""Screen capture through the xdg-desktop-portal ScreenCast API and PipeWire.

This is the same path GNOME uses for screen sharing. It needs no special
privileges; the first run shows GNOME's "share your screen" dialog, and later
runs reuse a saved restore token so the dialog is skipped.
"""

import os
import secrets
from pathlib import Path

from gi.repository import Gio, GLib

from .gst_frames import PipeWireFrames

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_MONITOR = 1
CURSOR_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "hurmat-tasaaweer"
RESTORE_TOKEN_FILE = STATE_DIR / "screencast_restore_token"


class PortalError(RuntimeError):
    pass


class ScreenCastPortal:
    """Minimal client for the ScreenCast portal's request/response protocol."""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session = None

    def _token(self):
        return "hurmat_" + secrets.token_hex(8)

    def _request(self, method, args_fmt, args, options):
        """Call a portal method and block until its Request emits Response."""
        token = self._token()
        options = dict(options, handle_token=GLib.Variant("s", token))
        request_path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"

        loop = GLib.MainLoop()
        result = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            result["code"], result["results"] = params.unpack()
            loop.quit()

        # Subscribe before calling so a fast response can't be missed.
        sub = self.bus.signal_subscribe(
            PORTAL_BUS, REQUEST_IFACE, "Response", request_path, None,
            Gio.DBusSignalFlags.NONE, on_response,
        )
        try:
            self.bus.call_sync(
                PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE, method,
                GLib.Variant(args_fmt, (*args, options)),
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)

        if result["code"] != 0:
            reason = {1: "cancelled by user", 2: "failed"}.get(result["code"], "unknown error")
            raise PortalError(f"ScreenCast.{method}: {reason}")
        return result["results"]

    def start(self):
        """Run CreateSession → SelectSources → Start. Returns the PipeWire node id."""
        results = self._request(
            "CreateSession", "(a{sv})", (),
            {"session_handle_token": GLib.Variant("s", self._token())},
        )
        self.session = results["session_handle"]

        select_opts = {
            "types": GLib.Variant("u", SOURCE_MONITOR),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_HIDDEN),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        if RESTORE_TOKEN_FILE.exists():
            select_opts["restore_token"] = GLib.Variant("s", RESTORE_TOKEN_FILE.read_text().strip())
        self._request("SelectSources", "(oa{sv})", (self.session,), select_opts)

        results = self._request("Start", "(osa{sv})", (self.session, ""), {})
        if "restore_token" in results:
            # Restore tokens are single-use; the portal hands back a fresh one each time.
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            RESTORE_TOKEN_FILE.write_text(results["restore_token"])

        streams = results.get("streams") or []
        if not streams:
            raise PortalError("ScreenCast.Start returned no streams")
        node_id, props = streams[0]
        return node_id, props

    def open_pipewire_remote(self):
        ret, fd_list = self.bus.call_with_unix_fd_list_sync(
            PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
        )
        return fd_list.get(ret.unpack()[0])


class ScreenCapture(PipeWireFrames):
    """Whole-monitor capture via the portal (includes anything drawn on top)."""

    def __init__(self):
        self.portal = ScreenCastPortal()
        node_id, props = self.portal.start()
        self.stream_size = props.get("size")
        super().__init__(node_id, fd=self.portal.open_pipewire_remote())
