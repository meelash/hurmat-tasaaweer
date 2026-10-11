#!/usr/bin/env bash
# CPU cost of hurmatd under load, in a headless session: three windows that
# redraw fully every frame on a 3440x1440 monitor. Extra args go to hurmatd.
# usage: cpu_bench.sh [hurmatd args...]
cd "$(dirname "$0")/.."
ARGS="$*" HURMAT_MONITOR=3440x1440 tools/headless_session.sh '
for i in 1 2 3; do python3 tools/busy_win.py 1100 1200 > /dev/null 2>&1 & done
sleep 2
SHELL_PID=$(pgrep -nx gnome-shell)
tools/cpu_usage.sh 5 gnome-shell=$SHELL_PID | sed "s/^/without hurmatd: /"
.venv/bin/python -m hurmat.daemon $ARGS > "$HURMAT_STATE/daemon.log" 2>&1 &
DAEMON_PID=$!
sleep 6
tools/cpu_usage.sh 10 gnome-shell=$SHELL_PID hurmatd=$DAEMON_PID | sed "s/^/with hurmatd:    /"
kill $(jobs -p) 2>/dev/null' 2>&1 | grep -E '^with'
