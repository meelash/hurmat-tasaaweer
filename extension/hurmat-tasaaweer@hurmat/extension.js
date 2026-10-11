// Draws blur over regions of windows reported by the hurmatd detector. Each
// region is a figure's outline (a stack of horizontal bands): blurred as a
// box, with sharp copies of the window laid over the parts outside the
// outline, so nearby buttons and text stay readable.
//
// Each window gets an overlay container placed directly above its actor in
// global.window_group, bound to the actor's position. That means:
//  - blur follows window moves immediately, without waiting for the detector;
//  - windows stacked above cover the blur the same way they cover the window;
//  - the overlay is not part of the window actor, so Mutter's per-window
//    screen cast (which hurmatd reads) sees the unblurred content and the
//    detector never ends up looking at its own blur.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {subtract, visibleWindows} from './visibility.js';

const BUS_NAME = 'org.hurmat.Tasaaweer';
// Owned by hurmatd while it runs.
const DAEMON_BUS_NAME = 'org.hurmat.Tasaaweer.Detector';
const OBJECT_PATH = '/org/hurmat/Tasaaweer';
const IFACE_XML = `
<node>
  <interface name="org.hurmat.Tasaaweer1">
    <!-- windows: (id, title, x, y, width, height, visible). The rectangle
         is the window's visible frame inside its screen-cast stream, in
         physical pixels: the part worth running detection on. "visible" is
         false when the window can't be seen (minimized, other workspace,
         fully covered). -->
    <method name="ListWindows">
      <arg type="a(tsiiiib)" direction="out" name="windows"/>
    </method>
    <!-- regions: (track id, bands). Bands (y0, y1, x0, x1) are non-overlapping
         horizontal slices covering a figure's outline, in the window
         stream's pixel coordinates (physical pixels relative to the
         window's buffer). -->
    <method name="SetRegions">
      <arg type="t" direction="in" name="window_id"/>
      <arg type="a(ua(dddd))" direction="in" name="regions"/>
    </method>
    <method name="ClearAll"/>
    <signal name="WindowsChanged"/>
    <signal name="VisibilityChanged">
      <arg type="t" name="window_id"/>
      <arg type="b" name="visible"/>
    </signal>
    <signal name="WindowResized">
      <arg type="t" name="window_id"/>
      <arg type="i" name="x"/>
      <arg type="i" name="y"/>
      <arg type="i" name="width"/>
      <arg type="i" name="height"/>
    </signal>
  </interface>
</node>`;

const BLUR_RADIUS = 48;

const TRACKED_TYPES = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.UTILITY,
]);

// One figure: a blur over the bounding box of its outline, with sharp
// copies of the window laid over the parts of the box outside the outline.
// Copies (Clutter.Clone) are much cheaper than extra blur effects.
class BlurRegion {
    constructor(container, windowActor) {
        this.container = container;
        this.windowActor = windowActor;
        this.blur = new St.Widget({reactive: false});
        this.blur.add_effect(new Shell.BlurEffect({
            mode: Shell.BlurMode.BACKGROUND,
            radius: BLUR_RADIUS,
            brightness: 1.0,
        }));
        container.add_child(this.blur);
        this.sharp = [];
    }

    setShape(bands, covered) {
        const x0 = Math.min(...bands.map(b => b[2])), y0 = Math.min(...bands.map(b => b[0]));
        const x1 = Math.max(...bands.map(b => b[3])), y1 = Math.max(...bands.map(b => b[1]));
        this.blur.set_position(x0, y0);
        this.blur.set_size(Math.max(1, x1 - x0), Math.max(1, y1 - y0));

        let pieces = [[x0, y0, x1, y1]];
        for (const c of covered)
            pieces = subtract(pieces, c);
        pieces = pieces.filter(([a, b, c, d]) => c - a >= 1 && d - b >= 1);

        while (this.sharp.length < pieces.length) {
            const clone = new Clutter.Clone({source: this.windowActor, reactive: false});
            this.container.add_child(clone);  // above every blur added before it
            this.sharp.push(clone);
        }
        while (this.sharp.length > pieces.length)
            this.sharp.pop().destroy();
        pieces.forEach(([a, b, c, d], i) => {
            this.sharp[i].set_clip(a, b, c - a, d - b);
            // Keep sharp copies above all blurs, including regions added later.
            this.container.set_child_above_sibling(this.sharp[i], null);
        });
    }

    destroy() {
        this.blur.destroy();
        this.sharp.forEach(c => c.destroy());
        this.sharp = [];
    }
}

class WindowOverlay {
    constructor(metaWindow, onResized) {
        this.metaWindow = metaWindow;
        this.windowActor = metaWindow.get_compositor_private();
        this.regions = new Map();

        this.container = new Clutter.Actor({reactive: false});
        this.container.add_constraint(new Clutter.BindConstraint({
            source: this.windowActor,
            coordinate: Clutter.BindCoordinate.POSITION,
        }));
        this.windowActor.bind_property('visible', this.container, 'visible',
            GObject.BindingFlags.SYNC_CREATE);
        global.window_group.insert_child_above(this.container, this.windowActor);

        this._sizeChangedId = metaWindow.connect('size-changed', () => {
            this._updateClip();
            onResized(this);
        });
        this._updateClip();
    }

    // Whether Mutter can safely record this window right now.
    recordable() {
        return !this.windowActor.is_destroyed() &&
            Boolean(this.windowActor.get_texture()?.get_texture());
    }

    // Visible frame within the window's buffer (and so within its screen-cast
    // stream), in physical pixels.
    contentRect() {
        const scale = this.windowActor.get_resource_scale() || 1;
        const buffer = this.metaWindow.get_buffer_rect();
        const frame = this.metaWindow.get_frame_rect();
        return [frame.x - buffer.x, frame.y - buffer.y, frame.width, frame.height]
            .map(v => Math.round(v * scale));
    }

    // Clip to the visible frame. The window actor (and the window stream) also
    // cover client-side shadows, so padded regions could otherwise spill out.
    _updateClip() {
        const buffer = this.metaWindow.get_buffer_rect();
        const frame = this.metaWindow.get_frame_rect();
        this.container.set_clip(frame.x - buffer.x, frame.y - buffer.y, frame.width, frame.height);
    }

    restack() {
        if (this.windowActor.get_parent() === global.window_group)
            global.window_group.set_child_above_sibling(this.container, this.windowActor);
    }

    // regions: [[id, shape]], shape = [[y0, y1, x0, x1], ...] horizontal
    // bands covering a figure's outline, in physical stream pixels.
    setRegions(regions) {
        const scale = this.windowActor.get_resource_scale() || 1;
        const shapes = new Map(regions.map(([id, bands]) =>
            [id, bands.map(b => b.map(v => v / scale))]));
        for (const [id, region] of this.regions) {
            if (!shapes.has(id)) {
                region.destroy();
                this.regions.delete(id);
            }
        }
        // Sharp patches must stay clear of *every* figure in the window, so
        // where two regions overlap one can't uncover the other.
        const covered = [...shapes.values()].flat().map(([y0, y1, x0, x1]) => [x0, y0, x1, y1]);
        for (const [id, bands] of shapes) {
            let region = this.regions.get(id);
            if (!region) {
                region = new BlurRegion(this.container, this.windowActor);
                this.regions.set(id, region);
            }
            region.setShape(bands, covered);
        }
    }

    destroy() {
        this.metaWindow.disconnect(this._sizeChangedId);
        this.container.destroy();
        this.regions.clear();
    }
}

export default class HurmatTasaaweer extends Extension {
    enable() {
        this._overlays = new Map();  // window id (bigint-safe string) → WindowOverlay
        this._signals = [];

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
        // If hurmatd stops (or crashes), its regions would stay frozen on
        // screen over content that may have moved on. Clear them.
        this._daemonWatchId = Gio.bus_watch_name_on_connection(Gio.DBus.session,
            DAEMON_BUS_NAME, Gio.BusNameWatcherFlags.NONE, null, () => this.ClearAll());

        this._visible = new Map();      // window id → last reported visibility
        this._windowSignals = new Map(); // MetaWindow → signal ids (all windows)
        this._visibilityIdle = 0;

        this._connect(global.display, 'window-created', (_d, win) => this._onWindowCreated(win));
        this._connect(global.display, 'restacked', () => {
            for (const overlay of this._overlays.values())
                overlay.restack();
            this._scheduleVisibility();
        });
        this._connect(global.workspace_manager, 'active-workspace-changed',
            () => this._scheduleVisibility());
        for (const actor of global.get_window_actors()) {
            this._watchGeometry(actor.meta_window);
            this._track(actor.meta_window);
        }
        this._scheduleVisibility();
    }

    disable() {
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        for (const [win, ids] of this._windowSignals)
            ids.forEach(id => win.disconnect(id));
        this._windowSignals = null;
        if (this._visibilityIdle)
            GLib.source_remove(this._visibilityIdle);
        Gio.bus_unwatch_name(this._daemonWatchId);
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = null;
        for (const overlay of this._overlays.values())
            overlay.destroy();
        this._overlays = null;
    }

    _connect(obj, signal, handler) {
        this._signals.push([obj, obj.connect(signal, handler)]);
    }

    // Any window (tracked or not) moving, resizing or minimizing can change
    // what's covered.
    _watchGeometry(metaWindow) {
        if (this._windowSignals.has(metaWindow))
            return;
        const schedule = () => this._scheduleVisibility();
        this._windowSignals.set(metaWindow, [
            metaWindow.connect('position-changed', schedule),
            metaWindow.connect('size-changed', schedule),
            metaWindow.connect('notify::minimized', schedule),
            metaWindow.connect('workspace-changed', schedule),
            metaWindow.connect('shown', schedule),
            metaWindow.connect('unmanaged', () => {
                this._windowSignals?.get(metaWindow)?.forEach(id => metaWindow.disconnect(id));
                this._windowSignals?.delete(metaWindow);
                schedule();
            }),
        ]);
    }

    _scheduleVisibility() {
        if (this._visibilityIdle)
            return;
        this._visibilityIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._visibilityIdle = 0;
            this._updateVisibility();
            return GLib.SOURCE_REMOVE;
        });
    }

    _updateVisibility() {
        if (!this._overlays)
            return;
        const visible = visibleWindows();
        for (const [key, overlay] of this._overlays) {
            const v = visible.has(overlay.metaWindow);
            if (this._visible.get(key) === v)
                continue;
            this._visible.set(key, v);
            this._dbus?.emit_signal('VisibilityChanged',
                new GLib.Variant('(tb)', [overlay.metaWindow.get_id(), v]));
        }
    }

    _onWindowCreated(metaWindow) {
        this._watchGeometry(metaWindow);
        // The actor may not exist yet when the window is created.
        if (metaWindow.get_compositor_private())
            this._track(metaWindow);
        else
            metaWindow.connect('shown', () => this._track(metaWindow));
    }

    _track(metaWindow) {
        if (!TRACKED_TYPES.has(metaWindow.get_window_type()))
            return;
        const key = String(metaWindow.get_id());
        if (this._overlays.has(key) || !metaWindow.get_compositor_private())
            return;
        const overlay = new WindowOverlay(metaWindow, o => {
            this._dbus?.emit_signal('WindowResized', new GLib.Variant('(tiiii)',
                [o.metaWindow.get_id(), ...o.contentRect()]));
        });
        this._overlays.set(key, overlay);
        // Visibility depends on the window's animations having finished.
        const actor = metaWindow.get_compositor_private();
        const animId = actor.connect('transitions-completed', () => this._scheduleVisibility());
        metaWindow.connect('unmanaged', () => {
            if (!actor.is_destroyed())
                actor.disconnect(animId);
        });
        // Don't offer a window for recording until it has drawn something:
        // Mutter's window screen cast crashes the whole shell if it records a
        // window with no buffer yet (seen with Firefox at startup).
        if (!overlay.recordable()) {
            const actor = metaWindow.get_compositor_private();
            const id = actor.connect('first-frame', () => {
                actor.disconnect(id);
                this._dbus?.emit_signal('WindowsChanged', null);
            });
        }
        metaWindow.connect('unmanaged', () => {
            this._overlays?.get(key)?.destroy();
            this._overlays?.delete(key);
            this._visible?.delete(key);
            this._dbus?.emit_signal('WindowsChanged', null);
        });
        this._dbus.emit_signal('WindowsChanged', null);
    }

    // --- D-Bus methods ---

    ListWindows() {
        const visible = visibleWindows();
        return [...this._overlays.values()].filter(o => o.recordable()).map(o =>
            [o.metaWindow.get_id(), o.metaWindow.get_title() ?? '', ...o.contentRect(),
                visible.has(o.metaWindow)]);
    }

    SetRegions(windowId, regions) {
        this._overlays.get(String(windowId))?.setRegions(regions);
    }

    ClearAll() {
        for (const overlay of this._overlays.values())
            overlay.setRegions([]);
    }
}
