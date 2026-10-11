#!/usr/bin/env bash
set -uo pipefail
UUID=hurmat-tasaaweer@hurmat
systemctl --user disable --now hurmatd.service 2>/dev/null
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hurmatd.service"
systemctl --user daemon-reload
gnome-extensions disable "$UUID" 2>/dev/null
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/hurmat-tasaaweer"
echo "Removed. Log out and back in to unload the extension completely."
