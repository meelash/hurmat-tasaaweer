#!/usr/bin/env bash
# Print CPU use (% of one core) of the given processes over N seconds.
# usage: cpu_usage.sh SECONDS NAME=PID [NAME=PID ...]
SECS=$1; shift
HZ=$(getconf CLK_TCK)
ticks() { awk '{print $14 + $15}' /proc/$1/stat; }
declare -A start
for kv in "$@"; do start[$kv]=$(ticks ${kv#*=}); done
sleep $SECS
out=""
for kv in "$@"; do out+="${kv%%=*}: $(( ($(ticks ${kv#*=}) - ${start[$kv]}) * 100 / HZ / SECS ))%  "; done
echo "$out"
