"""Pair latency_win.py SHOW times with hurmatd's first detection after each.
usage: latency_report.py WIN_LOG DAEMON_LOG"""
import re
import sys
from collections import Counter

shows = [float(m) for m in re.findall(r"^SHOW ([\d.]+)", open(sys.argv[1]).read(), re.M)]
frames = [(int(w), float(t), int(n)) for w, t, n in re.findall(
    r"window (\d+) \d+x\d+ at ([\d.]+): .*?→ (\d+) regions", open(sys.argv[2]).read())]
target = Counter(w for w, _, n in frames if n).most_common(1)[0][0] if frames else None

lat = []
for s in shows:
    hit = next((t for w, t, n in frames if w == target and t >= s and n), None)
    if hit is not None:
        lat.append((hit - s) * 1e3)
        print(f"shown {s:.3f} → blur sent +{lat[-1]:.0f}ms")
if lat:
    print(f"latency: mean {sum(lat)/len(lat):.0f}ms, max {max(lat):.0f}ms over {len(lat)} changes")
if frames:
    span = frames[-1][1] - frames[0][1]
    for w, c in Counter(w for w, _, _ in frames).items():
        print(f"window {w}: {c / span:.1f} frames/s processed{' (test window)' if w == target else ''}")
