#!/usr/bin/env bash
# Link the extension into the real GNOME session. On Wayland, GNOME Shell only
# picks up newly installed extensions at login, so log out and back in once
# after the first install, then run this again to enable it.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
UUID=hurmat-tasaaweer@hurmat
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions
mkdir -p "$DEST"
ln -sfn "$REPO/extension/$UUID" "$DEST/$UUID"
if gnome-extensions info "$UUID" >/dev/null 2>&1; then
    gnome-extensions enable "$UUID"
    echo "enabled $UUID"
else
    echo "linked $DEST/$UUID: log out and back in, then run this script again to enable it"
fi
