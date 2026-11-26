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
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

import matplotlib
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

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
