#!/usr/bin/env bash
# Install Hurmat Tasaaweer for GNOME (Wayland): the detector as a user
# service, and the GNOME Shell extension. Run from the unpacked download:
#   ./install.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/hurmat-tasaaweer
EXT_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
UUID=hurmat-tasaaweer@hurmat

missing=()
python3 -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst; Gst.init(None)' 2>/dev/null \
    || missing+=("GStreamer Python bindings")
gst-inspect-1.0 pipewiresrc >/dev/null 2>&1 || missing+=("PipeWire GStreamer plugin")
if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing: ${missing[*]}. Install them first:"
    echo "  Fedora:        sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good pipewire-gstreamer"
    echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-pipewire python3-venv"
    exit 1
fi

echo "Installing to $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/hurmat" "$DEST/models"
cp -r "$HERE/hurmat" "$HERE/models" "$HERE/LICENSE" "$HERE/NOTICE.md" "$HERE/uninstall.sh" "$DEST/"
if [ ! -x "$DEST/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$DEST/venv"
fi
"$DEST/venv/bin/pip" install --quiet --upgrade -r "$HERE/requirements-gnome.txt"

echo "Installing the GNOME Shell extension"
mkdir -p "$EXT_DIR"
rm -rf "$EXT_DIR/$UUID"
cp -r "$HERE/extension/$UUID" "$EXT_DIR/"

echo "Installing the detector service"
mkdir -p "$UNIT_DIR"
sed "s|@DEST@|$DEST|g" "$HERE/hurmatd.service" > "$UNIT_DIR/hurmatd.service"
systemctl --user daemon-reload
systemctl --user enable hurmatd.service >/dev/null

if gnome-extensions info "$UUID" >/dev/null 2>&1; then
    gnome-extensions enable "$UUID"
    systemctl --user restart hurmatd.service
    echo "Done. It's running now."
else
    echo
    echo "Almost done: log out and back in once (GNOME only notices new extensions at login),"
    echo "then run:  gnome-extensions enable $UUID"
    echo "The detector service starts by itself at login."
fi
echo "Logs: journalctl --user -u hurmatd -f      Uninstall: $DEST/uninstall.sh"
