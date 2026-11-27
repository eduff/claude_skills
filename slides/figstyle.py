"""figstyle — matplotlib conventions for talk-sized schematics.

Large type for slides, editable SVG text, and a few drawing primitives for
box-and-arrow diagrams. Pairs with slidekit: draw with figsize matching the
space the figure will fill on the slide, save PNG + SVG, place the PNG with
``Deck.picture`` / ``fig_right`` / ``fig_full``.

    from figstyle import use_talk_style, PALETTE, box, arrow, badge, save
    use_talk_style()
    fig, ax = plt.subplots(figsize=(14, 8)); ax.set_axis_off()
    box(ax, 0, 0, 3, 1, 'Exposure', PALETTE['blue'], tc='white')
    save(fig, 'pathways', outdir='figures')

Diagram layouts (each returns ``fig, ax``; pass ``ax=`` to draw into an existing axes):

    flow(steps, shape='circle'|'box'|'chevron', ncols=, side=[...])   process / pipeline
    cohort_flow(stages, exclusions)                                     STROBE/CONSORT participant flow
    dag(nodes, edges)                                                   causal diagram, edges with badges
    key(ax, x, y, rows)                                                 badge legend beside a diagram
"""
from __future__ import annotations

import math
import os
import warnings
from typing import Iterable, Optional

import matplotlib
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon

# Colour-blind-safe diverging pair plus accents (RdBu / PuOr family).
PALETTE = {
    'blue': '#4393c3', 'dark_blue': '#2166ac', 'red': '#d6604d', 'dark_red': '#b2182b',
    'purple': '#6a3d9a', 'green': '#5aae61', 'orange': '#e08214', 'gold': '#d4a017',
    'grey': '#888888', 'ink': '#222222', 'alert': '#c0392b',
}
GREY, INK, ALERT = PALETTE['grey'], PALETTE['ink'], PALETTE['alert']


def use_talk_style(font_size: float = 18, family: str = 'DejaVu Sans') -> None:
    """rcParams for slide figures: big type, thin axes, SVG text kept as text."""
    matplotlib.rcParams.update({
        'font.family': family, 'font.size': font_size, 'axes.linewidth': 1.0,
        'axes.spines.top': False, 'axes.spines.right': False,
        'svg.fonttype': 'none',  # text stays editable in PowerPoint/Illustrator
    })


def box(ax, x, y, w, h, text, fc, ec=None, fs=18, weight='normal', tc=INK, r=0.15, lw=1.2):
    """Rounded box with centred text."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}',
                                fc=fc, ec=ec or fc, lw=lw))
    ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=fs,
            weight=weight, color=tc, linespacing=1.3)


def arrow(ax, p, q, color=GREY, lw=2.5, rad=0.0, ms=24, style='-|>'):
    """Arrow from p to q; ``rad`` curves it."""
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=ms, color=color, lw=lw,
                                 connectionstyle=f'arc3,rad={rad}', shrinkA=0, shrinkB=0))


def badge(ax, x, y, label, fs=16, color=ALERT):
    """Numbered circle, for keying parts of a diagram to the text."""
    ax.text(x, y, str(label), fontsize=fs, ha='center', va='center', color='white', weight='bold',
            bbox=dict(boxstyle='circle,pad=0.25', fc=color, ec=color))


def save(fig, name: str, outdir: str = 'figures', exts: Iterable[str] = ('png', 'svg'),
         dpi: int = 200) -> None:
    """Save as PNG (for placing) and SVG (for editing), then close."""
    import matplotlib.pyplot as plt
    os.makedirs(outdir, exist_ok=True)
    for ext in exts:
        fig.savefig(os.path.join(outdir, f'{name}.{ext}'), dpi=dpi, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Diagram layouts. Coordinates are data units with equal aspect; when the
# function makes the figure, ``unit`` inches per data unit sets its size, so
# font sizes (points) stay in proportion to the shapes. Text that does not fit
# its shape is shrunk until it does.

_CYCLE = ('purple', 'red', 'blue', 'green', 'orange', 'grey')


def _steps(steps):
    out = []
    for i, s in enumerate(steps):
        if isinstance(s, str):
            s = (s, PALETTE[_CYCLE[i % len(_CYCLE)]])
        out.append((s[0], PALETTE.get(s[1], s[1])))
    return out


def _canvas(ax, xlim, ylim, unit):
    import matplotlib.pyplot as plt
    if ax is None:
        fig, ax = plt.subplots(figsize=((xlim[1] - xlim[0]) * unit, (ylim[1] - ylim[0]) * unit))
        fig.subplots_adjust(0, 0, 1, 1)
    ax.set_xlim(*xlim); ax.set_ylim(*ylim); ax.set_aspect('equal'); ax.set_axis_off()
    return ax.figure, ax


def _fit(pairs, min_fs=9, pad=0.86):
    """Shrink each (patch, text) text until it fits inside ``pad`` × the patch."""
    if not pairs:
        return
    fig = pairs[0][1].figure
    for ax in {t.axes for _, t in pairs}:
        ax.apply_aspect()
    r = fig.canvas.get_renderer()
    for p, t in pairs:
        pb = p.get_window_extent(r)
        k = pad * (0.82 if isinstance(p, Circle) else 1.0)
        while t.get_fontsize() > min_fs:
            tb = t.get_window_extent(r)
            if tb.width <= k * pb.width and tb.height <= k * pb.height:
                break
            t.set_fontsize(t.get_fontsize() * 0.94)


def _node(ax, x, y, w, h, text, fc, shape, fs, tc='white', weight='bold', ec='none', lw=1.5):
    if shape == 'circle':
        p = Circle((x, y), w / 2, fc=fc, ec=ec, lw=lw, zorder=3)
    elif shape == 'box':
        p = FancyBboxPatch((x - w / 2, y - h / 2), w, h, fc=fc, ec=ec, lw=lw, zorder=3,
                           boxstyle=f'round,pad=0,rounding_size={0.18 * min(w, h)}')
    elif shape == 'chevron':
        n = 0.3 * h
        p = Polygon([(x - w / 2, y - h / 2), (x + w / 2 - n, y - h / 2), (x + w / 2, y),
                     (x + w / 2 - n, y + h / 2), (x - w / 2, y + h / 2), (x - w / 2 + n, y)],
                    fc=fc, ec=ec, lw=lw, zorder=3)
    elif shape == 'diamond':  # flowchart decision
        p = Polygon([(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)],
                    fc=fc, ec=ec, lw=lw, zorder=3)
    else:
        raise ValueError(f'unknown shape {shape!r}')
    ax.add_patch(p)
    t = ax.text(x, y, text, ha='center', va='center', fontsize=fs, color=tc, weight=weight,
                linespacing=1.15, zorder=4)
    return p, t


def _boundary(c, w, h, shape, toward):
    """Point where the line from shape centre ``c`` towards ``toward`` leaves the shape."""
    dx, dy = toward[0] - c[0], toward[1] - c[1]
    if not (dx or dy):
        return c
    if shape == 'circle':
        s = (w / 2) / math.hypot(dx, dy)
    elif shape == 'diamond':
        s = 1 / (abs(dx) / (w / 2) + abs(dy) / (h / 2))
    else:
        s = min((w / 2) / abs(dx) if dx else math.inf, (h / 2) / abs(dy) if dy else math.inf)
    return c[0] + dx * s, c[1] + dy * s


def flow(steps, ax=None, shape='circle', ncols=None, size=4.0, gap=1.3, fs=24,
         side=None, side_title='', side_fs=18, side_colors=('#fff7d6', '#d9b84a', '#7a5c00'),
         arrow_color=INK, unit=0.8):
    """Process diagram: steps left to right, wrapping after ``ncols``.

    steps: labels or (label, colour) pairs; colours are hex or PALETTE names.
    shape: 'circle', 'box' (rounded) or 'chevron' (no arrows within a row).
    side:  bullet lines for a panel on the right, for things that apply at
           every step (bias controls, QC, data governance).
    """
    steps = _steps(steps)
    n = len(steps)
    ncols = ncols or n
    nrows = math.ceil(n / ncols)
    w, h = size, (size if shape == 'circle' else 0.5 * size)
    if shape == 'chevron':
        gap = min(gap, 0.06 * size)
    rowgap = max(gap * 1.6, 0.35 * h)
    pos = [((i % ncols) * (w + gap) + w / 2, -(i // ncols) * (h + rowgap)) for i in range(n)]
    width = min(n, ncols) * w + (min(n, ncols) - 1) * gap
    top, bottom = h / 2, -(nrows - 1) * (h + rowgap) - h / 2
    right = width
    if side:
        line = side_fs * 1.55 / 72 / unit
        need = (len(side) + (1.6 if side_title else 0.4)) * line + 0.6
        sw = max(1.5 * size, 0.62 * side_fs / 72 / unit * max(len(t) for t in side) + 1.0)
        mid = (top + bottom) / 2
        ph = max(top - bottom, need)
        top, bottom = max(top, mid + ph / 2), min(bottom, mid - ph / 2)
        px = width + max(gap, 0.8)
        right = px + sw
    fig, ax = _canvas(ax, (-0.3, right + 0.3), (bottom - 0.3, top + 0.3), unit)
    pairs = []
    for (x, y), (t, c) in zip(pos, steps):
        pairs.append(_node(ax, x, y, w, h, t, c, shape, fs))
    for i in range(n - 1):
        (x0, y0), (x1, y1) = pos[i], pos[i + 1]
        if y0 == y1:
            if shape != 'chevron':
                arrow(ax, (x0 + w / 2 + 0.1, y0), (x1 - w / 2 - 0.1, y1), color=arrow_color, lw=3, ms=28)
        else:  # wrap: down, back left, down into the next row
            ym = y0 - h / 2 - rowgap / 2
            ax.plot([x0, x0, x1], [y0 - h / 2 - 0.1, ym, ym], color=arrow_color, lw=3,
                    solid_capstyle='butt', solid_joinstyle='miter')
            arrow(ax, (x1, ym), (x1, y1 + h / 2 + 0.1), color=arrow_color, lw=3, ms=28)
    if side:
        fc, ec, tc = side_colors
        ax.add_patch(FancyBboxPatch((px, bottom), sw, top - bottom, fc=fc, ec=ec, lw=1.5,
                                    boxstyle='round,pad=0,rounding_size=0.25'))
        y = top - 0.3 - line * 0.6
        if side_title:
            ax.text(px + sw / 2, y, side_title, ha='center', va='center', fontsize=side_fs + 4,
                    weight='bold', color=tc)
            y -= line * 1.4
        for t in side:
            ax.text(px + 0.3, y, '•  ' + t, va='center', fontsize=side_fs, color=tc)
            y -= line
    _fit(pairs)
    return fig, ax


def cohort_flow(stages, exclusions=None, ax=None, width=6.5, excl_width=6.0, fs=17,
                colors=('#eef3f8', PALETTE['dark_blue']), excl_colors=('#fbeee9', PALETTE['dark_red']),
                arrow_color=INK, unit=1.0):
    """STROBE/CONSORT-style participant flow, top to bottom.

    stages:     (label, n) pairs (or labels), e.g. ('Recruited', 500000).
    exclusions: one entry per gap between stages: None, or a list of
                (reason, n) pairs / strings. The box header totals the n's.
    Units are inches by default (unit=1). Warns when a stage's n minus its
    exclusions does not equal the next stage's n.
    """
    exclusions = list(exclusions or []) + [None] * len(stages)
    line = fs * 1.4 / 72 / unit
    pad = 0.25

    def fmt(s):
        return f'{s[0]}\nn = {s[1]:,}' if isinstance(s, tuple) else s

    main = [fmt(s) for s in stages]
    mh = [line * (m.count('\n') + 1) + 2 * pad for m in main]
    ex = []
    for i in range(len(stages) - 1):
        e = exclusions[i]
        if not e:
            ex.append(None); continue
        nums = [r[1] for r in e if isinstance(r, tuple)]
        head = f'Excluded (n = {sum(nums):,})' if len(nums) == len(e) else 'Excluded'
        body = [f'{r[0]} (n = {r[1]:,})' if isinstance(r, tuple) else r for r in e]
        ex.append((head, body, line * (len(body) + 1.3) + 2 * pad))
        a, b = stages[i], stages[i + 1]
        if len(nums) == len(e) and isinstance(a, tuple) and isinstance(b, tuple) and a[1] - sum(nums) != b[1]:
            warnings.warn(f'cohort_flow: {a[0]!r} {a[1]:,} minus exclusions {sum(nums):,} '
                          f'!= {b[0]!r} {b[1]:,}')
    gaps = [max(0.9, (e[2] + 0.5) if e else 0) for e in ex]
    ys, y = [], 0.0
    for i, hgt in enumerate(mh):
        ys.append(y - hgt / 2)
        y -= hgt + (gaps[i] if i < len(gaps) else 0)
    xm = width / 2
    xe = width + 0.8
    right = xe + excl_width if any(ex) else width
    fig, ax = _canvas(ax, (-0.15, right + 0.15), (y - 0.15, 0.15), unit)
    for i, (m, yc, hgt) in enumerate(zip(main, ys, mh)):
        ax.add_patch(FancyBboxPatch((0, yc - hgt / 2), width, hgt, fc=colors[0], ec=colors[1], lw=1.8,
                                    boxstyle='round,pad=0,rounding_size=0.08'))
        lab = m.split('\n')
        ax.text(xm, yc + (line / 2 if len(lab) > 1 else 0), lab[0], ha='center', va='center',
                fontsize=fs, weight='bold', color=INK)
        if len(lab) > 1:
            ax.text(xm, yc - line / 2, '\n'.join(lab[1:]), ha='center', va='center', fontsize=fs,
                    color=INK, linespacing=1.25)
        if i < len(ex):
            y0, y1 = yc - hgt / 2, ys[i + 1] + mh[i + 1] / 2
            arrow(ax, (xm, y0), (xm, y1), color=arrow_color, lw=2.2, ms=20)
            if ex[i]:
                head, body, eh = ex[i]
                ye = (y0 + y1) / 2
                arrow(ax, (xm, ye), (xe, ye), color=arrow_color, lw=2.2, ms=20)
                ax.add_patch(FancyBboxPatch((xe, ye - eh / 2), excl_width, eh, fc=excl_colors[0],
                                            ec=excl_colors[1], lw=1.5,
                                            boxstyle='round,pad=0,rounding_size=0.08'))
                ty = ye + eh / 2 - pad - line / 2
                ax.text(xe + 0.2, ty, head, va='center', fontsize=fs, weight='bold', color=INK)
                for j, b in enumerate(body):
                    ax.text(xe + 0.2, ty - line * (j + 1.3), '•  ' + b, va='center',
                            fontsize=fs - 2, color=INK)
    return fig, ax


def dag(nodes, edges, ax=None, shape='box', size=3.4, fs=18, edge_color=INK, lw=3, ms=26,
        unit=0.75, margin=0.6):
    """Causal diagram (DAG) from positioned nodes and directed edges.

    nodes: {key: (x, y, colour[, label[, text_colour]])}; label defaults to the key.
    edges: (a, b) or (a, b, opts) with opts keys:
           color, lw, ls ('--' for unmeasured / assumed paths), rad (curvature),
           both (double-headed, for correlation), label (text at the midpoint),
           badge (number in a circle on the edge, keyed with ``key``), at (0-1 along edge).
    shape: 'box' (width size, height 0.4 size) or 'circle' (diameter size).
    """
    w, h = size, (size if shape == 'circle' else 0.4 * size)
    xs = [v[0] for v in nodes.values()]; ys = [v[1] for v in nodes.values()]
    xlim = (min(xs) - w / 2 - margin, max(xs) + w / 2 + margin)
    ylim = (min(ys) - h / 2 - margin, max(ys) + h / 2 + margin)
    fig, ax = _canvas(ax, xlim, ylim, unit)
    pairs = []
    for k, v in nodes.items():
        x, y, c = v[:3]
        lab = v[3] if len(v) > 3 else k
        tc = v[4] if len(v) > 4 else 'white'
        pairs.append(_node(ax, x, y, w, h, lab, PALETTE.get(c, c), shape, fs, tc=tc))
    for e in edges:
        a, b, o = (e[0], e[1], e[2] if len(e) > 2 else {})
        ca, cb = nodes[a][:2], nodes[b][:2]
        p = _boundary(ca, w + 0.25, h + 0.25, shape, cb)
        q = _boundary(cb, w + 0.25, h + 0.25, shape, ca)
        col = PALETTE.get(o.get('color', edge_color), o.get('color', edge_color))
        rad = o.get('rad', 0.0)
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle='<|-|>' if o.get('both') else '-|>',
                                     mutation_scale=ms, color=col, lw=o.get('lw', lw),
                                     ls=o.get('ls', '-'), connectionstyle=f'arc3,rad={rad}',
                                     shrinkA=0, shrinkB=0, zorder=2))
        if 'label' in o or 'badge' in o:
            mx, my = _on_arc(ax, p, q, rad, o.get('at', 0.5))
            if 'badge' in o:
                badge(ax, mx, my, o['badge'], fs=fs - 2)
            else:
                ax.text(mx, my, o['label'], ha='center', va='center', fontsize=fs - 2, color=col,
                        weight='bold', zorder=5,
                        bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none'))
    _fit(pairs)
    return fig, ax


def _on_arc(ax, p, q, rad, t):
    """Point a fraction ``t`` along an arc3 edge (arc3 bends in display space)."""
    import numpy as np
    ax.apply_aspect()
    P, Q = ax.transData.transform(p), ax.transData.transform(q)
    d = Q - P
    ctrl = (P + Q) / 2 + rad * np.array([d[1], -d[0]])
    xy = (1 - t) ** 2 * P + 2 * t * (1 - t) * ctrl + t ** 2 * Q
    return tuple(ax.transData.inverted().transform(xy))


def key(ax, x, y, rows, fs=18, step=None):
    """Legend for edge badges: rows of (badge, heading, detail) from (x, y) downwards.

    Place it inside the diagram's axes (extend xlim to make room) or in its own axes.
    """
    y0, y1 = ax.get_ylim()
    per_pt = abs(y1 - y0) / (ax.figure.get_size_inches()[1] * 72)   # data units per point
    step = step or fs * 4.4 * per_pt
    for i, (b, head, detail) in enumerate(rows):
        yy = y - i * step
        badge(ax, x, yy, b, fs=fs - 2)
        ax.text(x + fs * 2.2 * per_pt, yy, head, fontsize=fs, weight='bold', va='center', color=INK)
        if detail:
            ax.text(x + fs * 2.2 * per_pt, yy - fs * 0.9 * per_pt, detail, fontsize=fs - 3, va='top',
                    color='#333333', linespacing=1.25)
