# Hurmat Tasaaweer

Blurs pictures of living beings (people and animals) everywhere on your
screen, in every app, to help guard the gaze in line with the Shariah.
Nearby text and buttons stay sharp, and very small images are left alone.
Everything runs on your own computer; nothing is sent anywhere.

**Website:** <https://meelash.github.io/hurmat-tasaaweer/> ·
**Downloads:** [latest release](https://github.com/meelash/hurmat-tasaaweer/releases)

> [!WARNING]
> **Untested, except on GNOME.** The GNOME version is used day to day on
> Fedora (GNOME 50). The **Windows, macOS and X11 versions have only passed
> automatic checks on build machines** and may not work at all. Testing and
> reports are very welcome: please
> [send a test report](https://github.com/meelash/hurmat-tasaaweer/issues/new?template=test-report.yml),
> whether it worked or not. The author will work with willing testers to fix
> what they find.

## Platforms

| | Status | How it blurs |
|---|---|---|
| **Linux, GNOME (Wayland)** | Used daily (GNOME 50) | Inside the compositor, live, along each figure's outline |
| **Windows 10 (2004+) / 11** | Untested | Small always-on-top windows showing a blurred copy |
| **macOS 12+, Apple Silicon** | Untested | Same, as menu-bar app |
| **Linux, X11 desktops** | Lightly tested | Same, per window |
| Android | In development | |
| iPhone | Not possible | iOS doesn't let apps see or cover other apps |

The detector is a pair of YOLO11 models (outlines plus body pose), run with
ncnn on the GPU where possible. By default a person is blurred when a head or
most of a torso is visible; `--mode face` blurs only faces.

## Install

Downloads are on the [releases page](https://github.com/meelash/hurmat-tasaaweer/releases).
The builds aren't signed, so Windows and macOS warn about them the first time.

### Windows

1. Download `HurmatTasaaweer-windows-x64.zip` and unzip it anywhere.
2. Run `HurmatTasaaweer.exe` in the unzipped folder. If SmartScreen says
   "Windows protected your PC", choose **More info → Run anyway**.
3. A console window shows what it's doing; closing it (or Ctrl+C) stops the
   blurring.

### macOS

1. Download `HurmatTasaaweer-macos-arm64.zip` and unzip it. Move
   `HurmatTasaaweer.app` to Applications.
2. The app isn't notarized. Before the first start, run this in Terminal:
   `xattr -dr com.apple.quarantine /Applications/HurmatTasaaweer.app`
3. Open it. macOS asks for **Screen Recording** permission: allow it in
   System Settings → Privacy & Security → Screen Recording, then quit and
   reopen the app.
4. It lives in the menu bar as **HT**; quit it from there.

### Linux, X11 (XFCE, Cinnamon, MATE, i3, KDE on X11)

```sh
tar xzf HurmatTasaaweer-linux-x11-x64.tar.gz
./HurmatTasaaweer/HurmatTasaaweer
```

Stop it with Ctrl+C. To start it at login, add it to your desktop's
autostart settings.

### Linux, GNOME on Wayland

```sh
tar xzf HurmatTasaaweer-linux-gnome.tar.gz
cd HurmatTasaaweer-gnome && ./install.sh
```

The installer says if system packages are missing. After the first install,
log out and back in once, then run
`gnome-extensions enable hurmat-tasaaweer@hurmat`. The detector then starts
by itself at every login. `./uninstall.sh` removes everything.

### Options and self-test

All versions take the same options (`--help` lists them). For example,
`--mode face` blurs only faces and `--min-size 80` ignores smaller figures.
On GNOME, add options to the `ExecStart` line of
`~/.config/systemd/user/hurmatd.service`.

The Windows, macOS and X11 versions have a self-test:
`HurmatTasaaweer --self-test` (on macOS:
`HurmatTasaaweer.app/Contents/MacOS/HurmatTasaaweer --self-test`). It checks
that detection works and that the blur doesn't show up in screen captures,
and prints a report to paste into a test report.

## Reporting problems

Please [open a test report](https://github.com/meelash/hurmat-tasaaweer/issues/new?template=test-report.yml).
Describe images rather than attaching them, and don't post screenshots that
show unblurred people.

## How it works (GNOME)

```
 window content ──Mutter per-window screen cast──▶ hurmatd (YOLO on ncnn/Vulkan)
                                                      │ detections → tracker
                                                      │ (hold, pad, stable ids)
                                                      ▼  D-Bus SetRegions
 GNOME Shell extension: blur overlays above each window, pinned to it
```

- **`extension/`** is a GNOME Shell extension. For each window it adds an
  overlay just above the window in the stacking order, pinned to the window's
  position and clipped to its frame. Each figure is blurred along its outline,
  sent as a stack of horizontal bands. The outline's bounding box gets a
  `Shell.BlurEffect` in background mode. Sharp copies of the window
  (`Clutter.Clone`) are laid over the parts of the box outside every
  figure's outline. It exposes `org.hurmat.Tasaaweer` on the session bus
  (`ListWindows`, `SetRegions`, `WindowsChanged`, `VisibilityChanged`,
  `WindowResized`).
- **`hurmat/daemon.py`** (`hurmatd`) records each window through Mutter's
  ScreenCast API. A tracker turns detections into regions, then the daemon
  sends them to the extension.
  - Window streams show the window *without* the overlays, so the detector
    never sees its own blur. A whole-monitor capture would.
  - Frames only arrive when a window changes, so a static screen costs
    nothing.
  - Each frame is cropped to the window's visible frame (streams are padded
    to monitor size) and compared against the last checked frame
    (`hurmat/changes.py`). Only the changed area plus some context is run
    through the detector. Changes too small to hold a blur-worthy figure
    (cursor blink, streaming text) are skipped.
  - When a large area changes, the whole-area pass has to shrink it to the
    model's 640 px input, and small pictures (an 80 px avatar) become too
    small to recognise. A cheap pass (`hurmat/pictures.py`) finds
    picture-like blocks (many distinct colours, unlike text and interface).
    Small pictures are checked at full size, packed together into 640 px
    mosaics. Large picture areas are checked in tiles at two-thirds scale.
    The whole-area pass's results are sent first, so large figures aren't
    delayed by the extra runs (at most 10 per check).
  - Checks run one at a time, least-recently-checked window first. New
    content in a quiet window jumps ahead of windows that change every frame.
  - Windows you can't see (minimized, other workspace, fully covered) stop
    being recorded 1 s after they're hidden. A fresh recording starts the
    moment they're revealed. Windows mid-animation (opening, minimizing,
    restoring) count as hidden, because Mutter's window recording includes
    the animation's scaling and a check then puts the blur in the wrong
    place. Their blur stays in place meanwhile. The extension
    works out visibility (`visibility.js`).
  - Each window stream is capped at 15 fps (`--max-fps`). Mutter only
    enforces a gap between frames, so a quiet window that changes is still
    recorded at once; only constantly changing windows are throttled.
  - Frames are read in place: mapped through libgstreamer directly, because
    PyGObject only hands out copies. Only the sampled thumbnail and the crop
    being checked are ever read.
- **Who is blurred** (`hurmat/people.py`): a pose model (YOLO11s-pose) marks
  each person's body keypoints.
  - `--mode figure` (default): a person's whole outline is blurred if a head
    or most of a torso is visible. A hand, fingers or legs on their own are
    left alone. Headless torsos stay blurred on purpose: revealing bodies
    matter for guarding the gaze even without a head.
  - `--mode face`: only an oval around the head.
  - Animals are always blurred whole.
  - A person only one of the two models found is kept only if that model was
    confident (0.5+).
  - `--pose-model none` turns this off and blurs every detected person whole.
- **Blur behaviour:** only detected figures are blurred, along their outline
  (YOLO11s-seg; `hurmat/shapes.py`), never the whole image. A region appears
  instantly. While a figure moves, its blur covers both its current and its
  previous outline, so it trails slightly and stays covered. Outlines are
  padded by 3% + 10 px. A region is held for 0.3 s after the first missed
  detection to stop flicker (`--hold`). Figures under 48 px (longest side)
  are left alone.

## How it works (Windows, macOS, X11)

`hurmat/desktop/` shares the detection core (`hurmat/engine.py`) with the
GNOME version but has no compositor to blur inside. It captures the screen
and draws its own small, click-through windows over each figure, showing a
strongly blurred copy of the pixels underneath.

- **Windows:** each monitor is captured with `mss`. Blur windows are layered
  `WS_EX_TRANSPARENT` windows marked `WDA_EXCLUDEFROMCAPTURE`, so they're
  left out of captures and the detector never sees its own blur.
- **macOS:** each display is captured with `CGWindowListCreateImage`. Blur
  windows have `sharingType = none` for the same reason.
- **X11:** each top-level window is captured from its Composite pixmap, so
  the blur windows (override-redirect, input-transparent, shaped) aren't in
  it. They're clipped to the part of their window that isn't covered.

The blurred copy goes stale if what's under it changes (a video), until the
next check refreshes it. Captures are capped at 10 per second per monitor
(`--max-fps`). New content shows unblurred for one check, longer than on
GNOME because whole monitors are captured and compared.

## Development setup (GNOME)

```sh
sudo dnf install python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good pipewire-gstreamer
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r prototype/requirements.txt
tools/export_models.sh          # one-off; pulls in torch for the export only
tools/install_extension.sh      # then log out/in once, and run it again to enable
```

Desktop version from source: `pip install -r requirements-desktop.txt`,
then `python -m hurmat.desktop` (`--self-test` to check it). Packaged builds:
`pyinstaller packaging/hurmat-tasaaweer.spec`, also done by GitHub Actions
(`.github/workflows/build.yml`) on every push; tags `v*` publish a release.

## Running from source (GNOME)

```sh
.venv/bin/python -m hurmat.daemon          # -v for per-frame logging
.venv/bin/python -m hurmat.daemon --help   # model, thresholds, hold time, min size
```

The daemon reconnects by itself when the extension reloads or the screen is
unlocked. Mutter refuses screen-cast sessions while the screen is locked.

GNOME Shell caches extension code. After changing `extension/`, log out and
back in to load the new version.

**Performance**, measured in a headless session on a 3440×1440 virtual monitor
with three other windows redrawing fully every frame (a worst case). It's
the default model `yolo11s_ncnn_model` on the RX 5700:

- New image → blur sent: 119 ms mean, 180 ms worst, with the pose check
  (`tools/latency_win.py`, `tools/latency_report.py`). Without it
  (`--pose-model none`): 84 ms mean, 108 ms worst.
- A hidden window that's revealed is checked within ~45 ms.
- CPU: hurmatd 65% of one core. GNOME Shell goes from 11% to 32%
  (`tools/cpu_bench.sh`). With the busy windows covered, both drop to a
  few percent. A static desktop costs almost nothing.

## Development

- `tools/headless_session.sh '<cmd>'` runs a command inside a throwaway
  headless GNOME Shell with the extension enabled. It has its own D-Bus,
  settings and data, so the real desktop is untouched.
- `tools/testwin.py IMAGE` opens a test window.
- `tools/grab_monitor.py monitor out.png` saves what the virtual monitor
  shows.

  ```sh
  tools/headless_session.sh '
    python3 tools/testwin.py photo.jpg & sleep 2
    .venv/bin/python -m hurmat.daemon & sleep 6
    python3 tools/grab_monitor.py monitor /tmp/out.png; kill %2 %1'
  ```
- `tools/cpu_bench.sh [hurmatd args]` measures CPU use under load.
- `tools/busy_win.py` and `tools/latency_win.py` are load and latency test
  windows. `tools/latency_report.py` pairs their timestamps with hurmatd's
  `-v` log.
- `prototype/main.py` is the step 1 benchmark: whole-screen capture through
  the portal, with detections and timing printed.

## Known gaps

- **Buttons drawn over a figure stay blurred** (e.g. a video call's controls
  over someone's shoulders), since the outline follows the figure under
  them.
- **New content shows unblurred for one detection cycle**, about 80–110 ms.
  Fixing that needs Mutter-level changes so new content is blurred until
  checked.
- **Busy windows are checked about 10 times a second each** when several
  change every frame, so blur on a moving figure in a video trails slightly.
  Padding and the eased motion cover most of it.
- **See-through windows count as opaque** when working out what's covered. A
  window behind a terminal with a transparent background isn't checked.
- **GNOME's screen-sharing indicator stays on** while hurmatd runs. Mutter
  shows it for every recording session.
- **Overview window previews aren't blurred.** They're small, but not tiny.
- **No tamper resistance yet:** any session process can call `SetRegions` /
  `ClearAll`, and the daemon runs as a normal user process.
- **A new window's pictures show unblurred during its opening animation**
  (~0.25 s), plus one check, since checks wait for the window to settle.
- Close-up faces and cartoons are weak spots for the detector.
- **Occasional false blurs** (e.g. brown paper bags read as a person, at
  low confidence). Raising `--conf` or switching to `yolo11m-seg` removes
  them, but costs real people (`--conf`) or cartoons and speed (`m-seg`).
  The fix is training on collected examples.

## License

AGPL-3.0 (see `LICENSE`). The bundled YOLO11 models are © Ultralytics under
AGPL-3.0; see `NOTICE.md` for all third-party notices.
