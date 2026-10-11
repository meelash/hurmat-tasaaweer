// Which windows can actually be seen right now: on the active workspace, not
// minimized, not mid-animation, and not completely covered by windows
// stacked above them.
//
// hurmatd pauses recording for the rest. A window that keeps changing behind
// another one (a video, a scrolling terminal) would otherwise cost a frame
// copy in the compositor and a detection pass for nothing.
//
// Windows being animated (opening, minimizing, restoring) count as not
// visible: Mutter's window screen cast records the animation's scaling, so a
// check during it puts the blur in the wrong place, and nothing re-checks
// once the animation ends without new content. hurmatd starts a fresh
// recording when the window settles instead.
//
// Covering windows are treated as opaque unless their actor is translucent;
// a window whose own content is see-through (e.g. a transparent terminal
// background) would hide what's behind it from detection.

import Meta from 'gi://Meta';

export function visibleWindows() {
    const workspace = global.workspace_manager.get_active_workspace();
    const stack = global.display.sort_windows_by_stacking(global.display.list_all_windows())
        .filter(w => isShown(w, workspace));
    const screen = [0, 0, global.stage.width, global.stage.height];

    const visible = new Set();
    const covers = [];
    // Top of the stack first, accumulating what's covered so far.
    for (let i = stack.length - 1; i >= 0; i--) {
        const w = stack[i];
        const rect = intersect(frameRect(w), screen);
        let uncovered = rect ? [rect] : [];
        for (const cover of covers) {
            if (!uncovered.length)
                break;
            uncovered = subtract(uncovered, cover);
        }
        if (uncovered.length)
            visible.add(w);
        if (rect && isOpaque(w))
            covers.push(rect);
    }
    return visible;
}

function isShown(w, workspace) {
    const actor = w.get_compositor_private();
    return Boolean(actor?.visible) && !w.minimized && w.located_on_workspace(workspace) &&
        w.get_window_type() !== Meta.WindowType.DESKTOP && isSettled(actor);
}

export function isSettled(actor) {
    return actor.scale_x === 1 && actor.scale_y === 1 &&
        !actor.get_transition('scale-x') && !actor.get_transition('scale-y') &&
        !actor.get_transition('opacity');
}

function isOpaque(w) {
    return w.get_compositor_private().opacity === 255;
}

function frameRect(w) {
    const r = w.get_frame_rect();
    return [r.x, r.y, r.x + r.width, r.y + r.height];
}

function intersect(a, b) {
    const r = [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
    return r[0] < r[2] && r[1] < r[3] ? r : null;
}

// Rectangles [x0, y0, x1, y1] minus `cover`, as non-overlapping pieces.
export function subtract(rects, cover) {
    const out = [];
    for (const r of rects) {
        const overlap = intersect(r, cover);
        if (!overlap) {
            out.push(r);
            continue;
        }
        const [ox0, oy0, ox1, oy1] = overlap;
        if (r[1] < oy0)
            out.push([r[0], r[1], r[2], oy0]);  // above
        if (oy1 < r[3])
            out.push([r[0], oy1, r[2], r[3]]);  // below
        if (r[0] < ox0)
            out.push([r[0], oy0, ox0, oy1]);    // left
        if (ox1 < r[2])
            out.push([ox1, oy0, r[2], oy1]);    // right
    }
    return out;
}
