#!/usr/bin/env bash
# Run a command inside a throwaway headless GNOME Shell with the extension
# enabled. Uses its own D-Bus session, settings and data dirs, so nothing
# touches the real desktop. Inside the command, WAYLAND_DISPLAY points at the
# headless shell and $HURMAT_STATE holds the shell log.
#
#   tools/headless_session.sh 'python3 tools/testwin.py photo.jpg & sleep 2; ...'
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
STATE=${HURMAT_STATE:-$(mktemp -d)}
mkdir -p "$STATE/data/gnome-shell/extensions" "$STATE/config"
ln -sfn "$REPO/extension/hurmat-tasaaweer@hurmat" "$STATE/data/gnome-shell/extensions/"

exec dbus-run-session -- env GSETTINGS_BACKEND=keyfile XDG_DATA_HOME="$STATE/data" \
    XDG_CONFIG_HOME="$STATE/config" HURMAT_STATE="$STATE" REPO="$REPO" CMD="$1" bash -c '
    gsettings set org.gnome.shell welcome-dialog-last-shown-version "999"
    gsettings set org.gnome.shell enabled-extensions "[\"hurmat-tasaaweer@hurmat\"]"
    gnome-shell --headless --virtual-monitor ${HURMAT_MONITOR:-1280x720} --wayland-display hurmat-headless \
        > "$HURMAT_STATE/shell.log" 2>&1 &
    SHELL_PID=$!
    for _ in $(seq 50); do
        gdbus introspect --session --dest org.hurmat.Tasaaweer --object-path /org/hurmat/Tasaaweer >/dev/null 2>&1 && break
        sleep 0.2
    done
    # GNOME starts in the overview; leave it so windows are shown normally.
    gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.freedesktop.DBus.Properties.Set org.gnome.Shell OverviewActive "<false>" >/dev/null 2>&1 || true
    cd "$REPO"
    export WAYLAND_DISPLAY=hurmat-headless GDK_BACKEND=wayland
    bash -c "$CMD"; status=$?
    kill $SHELL_PID; wait $SHELL_PID 2>/dev/null
    exit $status'
