"""slidekit — editable PowerPoint decks from Python, on your own template.

A thin layer over python-pptx with the layout and typography conventions of a
clean scientific talk: assertion titles, bold-lead bullets, a figure beside the
text, rounded "cards", big headline numbers, shaded equation and model panels,
and native (editable) charts. Every shape is a normal PowerPoint object, so the
deck can be finished by hand.

    from slidekit import Deck, B, N
    deck = Deck('template.pptx', layout='Title & Bullets', keep_slides=1)
    s = deck.new('Risk doubles every five years of age', notes='Details for the speaker...')
    deck.fig_right(s, 'fig.png', [[B('Age dominates: '), N('doubling every ~4.4 y')]])
    deck.save('talk.pptx')

Units
-----
Positions and sizes are in inches. The content frame ``deck.L, deck.TOP,
deck.R, deck.BOT`` is derived from the slide size (and the template's title
placeholder when there is one), so layouts written against the frame work on
any canvas.

Font sizes and the small fixed offsets inside cards and panels are given for a
**15-inch-tall reference canvas** (the Keynote-export 26.67 x 15 in size) and
scaled by ``deck.k = slide_height / 15``. On a standard 13.33 x 7.5 in
PowerPoint canvas every size is halved automatically, so the same script gives
the same look on either.

Requires python-pptx (>= 0.6.21), Pillow, lxml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import (XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION,
                             XL_TICK_LABEL_POSITION)
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

EMU_PER_IN = 914400
REF_HEIGHT_IN = 15.0

Run = Tuple[str, dict]
Para = Union[str, List[Run]]


def rgb(hex_or_rgb) -> RGBColor:
    """'#6A3D9A' / '6A3D9A' / RGBColor -> RGBColor."""
    if isinstance(hex_or_rgb, RGBColor):
        return hex_or_rgb
    return RGBColor.from_string(hex_or_rgb.lstrip('#').upper())


def B(t: str, **o) -> Run:
    """Bold run. Options: color, size, italic."""
    return (t, {'bold': True, **o})


def N(t: str, **o) -> Run:
    """Normal run. Options: color, size, italic, bold."""
    return (t, o)


@dataclass
class Theme:
    """Colours, font and type scale. Sizes are points on the 15-in reference canvas."""
    font: str = 'Arial'
    ink: str = '#222222'          # body text
    muted: str = '#555555'        # captions, secondary text
    card: str = '#F2F2F4'         # card / panel fill
    rule: str = '#AAAAAA'         # placeholder outlines
    grid: str = '#DDDDDD'         # chart gridlines
    accents: Dict[str, str] = field(default_factory=lambda: {
        'purple': '#6A3D9A', 'red': '#D6604D', 'blue': '#4393C3',
        'green': '#5AAE61', 'dark': '#2E2350'})
    title: float = 48
    body: float = 28
    head: float = 32               # card headings
    small: float = 22              # captions, stat labels, chart text
    stat: float = 72               # headline numbers
    para_space: float = 14         # space before each paragraph (pt)

    def accent(self, name_or_hex) -> RGBColor:
        return rgb(self.accents.get(name_or_hex, name_or_hex))


class Deck:
    """A presentation plus layout helpers bound to its canvas and theme.

    template     .pptx/.potx to build on; None for a blank 16:9 deck
    layout       name of the slide layout for content slides (first match);
                 defaults to 'Title Only' on a blank deck
    keep_slides  how many of the template's existing slides to keep (e.g. 1
                 keeps its title slide); the rest are dropped
    """

    def __init__(self, template: Optional[str] = None, layout: Optional[str] = None,
                 keep_slides: int = 0, theme: Optional[Theme] = None,
                 margins: Tuple[float, float, float, float] = (0.049, 0.193, 0.048, 0.06)):
        self.theme = theme or Theme()
        self._blank = not template
        if template:
            self.prs = Presentation(template)
        else:
            self.prs = Presentation()
            self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
            layout = layout or 'Title Only'
        names = [l.name for l in self.prs.slide_layouts]
        if layout:
            try:
                self.layout = next(l for l in self.prs.slide_layouts if l.name == layout)
            except StopIteration:
                raise ValueError(f'layout {layout!r} not in template; available: {names}')
        else:
            self.layout = self.prs.slide_layouts[min(5, len(names) - 1)]
        self._drop_slides(keep_slides)

        self.W = self.prs.slide_width / EMU_PER_IN
        self.H = self.prs.slide_height / EMU_PER_IN
        self.k = self.H / REF_HEIGHT_IN
        ml, mt, mr, mb = margins
        self.L, self.R = ml * self.W, self.W - mr * self.W
        self.TOP, self.BOT = mt * self.H, self.H - mb * self.H
        # Below the layout's title placeholder, if that is lower than the default.
        for ph in ([] if self._blank else self.layout.placeholders):
            if ph.placeholder_format.type is not None and 'TITLE' in str(ph.placeholder_format.type):
                self.TOP = max(self.TOP, (ph.top + ph.height) / EMU_PER_IN + 0.03 * self.H)
                break

    # ------------------------------------------------------------ basics
    @property
    def width(self) -> float:
        return self.R - self.L

    @property
    def height(self) -> float:
        return self.BOT - self.TOP

    def pt(self, size: float) -> Pt:
        return Pt(size * self.k)

    def d(self, inches: float) -> float:
        """Scale a reference-canvas offset (inches) to this canvas."""
        return inches * self.k

    def _drop_slides(self, keep: int) -> None:
        ids = self.prs.slides._sldIdLst
        for sid in list(ids)[keep:]:
            self.prs.part.drop_rel(sid.rId)
            ids.remove(sid)

    def new(self, title: str, notes: str = '', size: Optional[float] = None):
        """Add a content slide with an assertion title and optional speaker notes."""
        s = self.prs.slides.add_slide(self.layout)
        if s.shapes.title is not None:
            t = s.shapes.title
            if self._blank:  # default layouts are drawn for 10-in 4:3; refit the title to the frame
                t.left, t.width = Inches(self.L), Inches(self.width)
                t.top, t.height = Inches(self.d(0.9)), Inches(self.TOP - self.d(1.2))
                t.text_frame.vertical_anchor = MSO_ANCHOR.BOTTOM
            t.text = title
            if self._blank:
                t.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            for r in t.text_frame.paragraphs[0].runs:
                r.font.size = self.pt(size or self.theme.title)
                r.font.name = self.theme.font
        else:
            self.text(s, self.L, self.d(0.9), self.width, self.TOP - self.d(1.2), [title],
                      size=size or self.theme.title, bold=True, anchor=MSO_ANCHOR.BOTTOM)
        if notes:
            self.notes(s, notes)
        return s

    @staticmethod
    def notes(slide, text: str) -> None:
        slide.notes_slide.notes_text_frame.text = text

    def retitle(self, slide, replacements: Dict[str, str]) -> None:
        """Replace text in a template slide (e.g. its title slide) keeping the
        first run's formatting. Keys are prefixes of the existing text."""
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            tf = sh.text_frame
            new = next((v for k, v in replacements.items() if tf.text.startswith(k)), None)
            if new is None:
                continue
            p0 = tf.paragraphs[0]
            for extra in tf.paragraphs[1:]:
                extra._p.getparent().remove(extra._p)
            runs = p0.runs
            if not runs:
                p0.text = new
                continue
            runs[0].text = new
            for r in runs[1:]:
                r._r.getparent().remove(r._r)

    def save(self, path: str) -> str:
        self.prs.save(path)
        return path

    # ------------------------------------------------------------ text
    def _bullet(self, p, on: bool) -> None:
        pPr = p._p.get_or_add_pPr()
        for tag in ('a:spcBef', 'a:buNone', 'a:buChar', 'a:buFont'):
            for el in pPr.findall(qn(tag)):
                pPr.remove(el)
        spc = etree.SubElement(pPr, qn('a:spcBef'))
        etree.SubElement(spc, qn('a:spcPts')).set('val', str(int(self.theme.para_space * self.k * 100)))
        if on:
            ind = Inches(self.d(0.45))
            pPr.set('marL', str(ind)); pPr.set('indent', str(-ind))
            etree.SubElement(pPr, qn('a:buFont')).set('typeface', self.theme.font)
            etree.SubElement(pPr, qn('a:buChar')).set('char', '•')
        else:
            pPr.set('marL', '0'); pPr.set('indent', '0')
            etree.SubElement(pPr, qn('a:buNone'))

    def text(self, slide, x, y, w, h, paras: Sequence[Para], size: Optional[float] = None,
             bullets: bool = False, color=None, bold: bool = False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name: Optional[str] = None):
        """Text box with zero margins. ``paras`` is a list of paragraphs, each a
        string or a list of runs built with B() / N()."""
        th = self.theme
        size = size or th.body
        color = rgb(color or th.ink)
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            tb.name = name
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        for m in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'):
            setattr(tf, m, 0)
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            self._bullet(p, bullets)
            for t, o in ([(para, {})] if isinstance(para, str) else para):
                r = p.add_run()
                r.text = t
                f = r.font
                f.name = th.font
                f.size = self.pt(o.get('size', size))
                f.bold = o.get('bold', bold)
                f.italic = o.get('italic', False)
                c = o.get('color')
                f.color.rgb = th.accent(c) if isinstance(c, str) else (c or color)
        return tb

    def bullets(self, slide, x, y, w, h, paras, size=None, **kw):
        return self.text(slide, x, y, w, h, paras, size=size, bullets=True, **kw)

    # ------------------------------------------------------------ shapes
    def _rounded(self, slide, x, y, w, h, radius=0.06, fill=None):
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.adjustments[0] = radius
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill or self.theme.card)
        sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def picture(self, slide, path: str, x, y, w, h, name: Optional[str] = None):
        """Image scaled to fit inside the box, centred, aspect ratio kept."""
        iw, ih = Image.open(path).size
        s = min(w / iw, h / ih)
        pw, ph = iw * s, ih * s
        pic = slide.shapes.add_picture(path, Inches(x + (w - pw) / 2), Inches(y + (h - ph) / 2),
                                       Inches(pw), Inches(ph))
        pic.name = name or os.path.basename(path)
        return pic

    def card(self, slide, x, y, w, h, head: str, body: Sequence[Para], accent='dark',
             size: Optional[float] = None, bullets: bool = True):
        """Rounded grey card: accent-coloured heading over bullets."""
        d, th = self.d, self.theme
        self._rounded(slide, x, y, w, h)
        self.text(slide, x + d(0.45), y + d(0.4), w - d(0.9), d(0.8), [head],
                  size=th.head, bold=True, color=th.accent(accent))
        self.text(slide, x + d(0.45), y + d(1.35), w - d(0.9), h - d(1.6), body,
                  size=size or th.body, bullets=bullets)

    def stat(self, slide, x, y, w, big: str, label: str, accent='purple', size: Optional[float] = None):
        """Headline number with a one-line label underneath."""
        d, th = self.d, self.theme
        self.text(slide, x, y, w, d(1.3), [big], size=size or th.stat, bold=True, color=th.accent(accent))
        self.text(slide, x, y + d(1.35), w, d(1.2), [label], size=th.small)

    def stat_row(self, slide, y, items: Iterable[Tuple[str, str, str]], size: Optional[float] = None,
                 gap: float = 0.35):
        """Evenly spaced stats across the frame: items = [(big, label, accent), ...]."""
        items = list(items)
        w = (self.width - self.d(gap) * (len(items) - 1)) / len(items)
        for i, (big, lab, acc) in enumerate(items):
            self.stat(slide, self.L + i * (w + self.d(gap)), y, w, big, lab, acc, size=size)

    def cards_row(self, slide, y, h, items: Iterable[Tuple[str, Sequence[Para], str]],
                  size: Optional[float] = None, gap: float = 0.4):
        """Side-by-side cards across the frame: items = [(head, body, accent), ...]."""
        items = list(items)
        w = (self.width - self.d(gap) * (len(items) - 1)) / len(items)
        for i, (head, body, acc) in enumerate(items):
            self.card(slide, self.L + i * (w + self.d(gap)), y, w, h, head, body, acc, size=size)

    def equation(self, slide, x, y, w, eq: str, size: Optional[float] = None, accent='dark'):
        """One-line shaded equation strip."""
        d = self.d
        h = d(1.1)
        self._rounded(slide, x, y, w, h, radius=0.15)
        self.text(slide, x + d(0.4), y, w - d(0.8), h, [eq], size=size or self.theme.body,
                  color=self.theme.accent(accent), anchor=MSO_ANCHOR.MIDDLE)

    def panel(self, slide, x, y, w, h, heading: str, eq: Sequence[str], defs: Sequence[Para],
              eq_size: float = 40, accent='purple'):
        """Shaded panel: heading, a centred multi-line equation, then symbol definitions."""
        d, th = self.d, self.theme
        self._rounded(slide, x, y, w, h, radius=0.05)
        self.text(slide, x + d(0.45), y + d(0.3), w - d(0.9), d(0.85), [heading],
                  size=th.head, bold=True, color=th.accent(accent))
        eh = d(0.75 * len(eq) * eq_size / 40 + 0.25)
        self.text(slide, x + d(0.45), y + d(1.2), w - d(0.9), eh, list(eq), size=eq_size,
                  color=th.accent('dark'), align=PP_ALIGN.CENTER)
        self.text(slide, x + d(0.45), y + d(1.5) + eh, w - d(0.9), h - d(1.7) - eh, defs, size=th.small + 2)

    def placeholder(self, slide, x, y, w, h, label: str, sub: str = '', name: str = 'Figure placeholder'):
        """Dashed box marking where a figure is to be pasted by hand."""
        th = self.theme
        ph = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        ph.fill.background(); ph.line.color.rgb = rgb(th.rule); ph.line.dash_style = 4
        ph.shadow.inherit = False
        ph.name = name
        paras: List[Para] = [label] + ([[N(sub, size=th.small, color=rgb(th.muted))]] if sub else [])
        self.text(slide, x + w * 0.04, y + h * 0.35, w * 0.92, h * 0.3, paras, size=th.body - 2,
                  color=th.muted, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        return ph

    # ------------------------------------------------------------ layouts
    def fig_right(self, slide, path: str, paras: Sequence[Para], size: Optional[float] = None,
                  split: Optional[float] = None):
        """Bullets on the left, figure filling the right. ``split`` = width of
        the text column in inches (default ~42% of the frame)."""
        split = split if split is not None else 0.42 * self.width
        self.text(slide, self.L, self.TOP + self.d(0.2), split - self.d(0.4), self.height, paras,
                  size=size or (self.theme.body + 2), bullets=True)
        self.picture(slide, path, self.L + split, self.TOP, self.width - split, self.height)

    def fig_full(self, slide, path: str, caption: Optional[str] = None, h: Optional[float] = None):
        """Figure across the whole frame, with an optional muted caption underneath."""
        h = h or (self.height - self.d(1.3) if caption else self.height)
        self.picture(slide, path, self.L, self.TOP, self.width, h)
        if caption:
            self.text(slide, self.L, self.TOP + h + self.d(0.3), self.width, self.d(1.0), [caption],
                      size=self.theme.small + 2, color=self.theme.muted)

    # ------------------------------------------------------------ charts
    def column_chart(self, slide, x, y, w, h, categories: Sequence[str],
                     series: Dict[str, Sequence[float]], title: Optional[str] = None,
                     colors: Optional[Sequence[str]] = None, number_format: str = '0.00',
                     ymin: Optional[float] = None, ymax: Optional[float] = None,
                     labels: bool = True):
        """Native clustered column chart (editable in PowerPoint, data in the file)."""
        th = self.theme
        cd = CategoryChartData()
        cd.categories = list(categories)
        for name, vals in series.items():
            cd.add_series(name, tuple(vals))
        ch = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x), Inches(y),
                                    Inches(w), Inches(h), cd).chart
        f = lambda font, s: (setattr(font, 'size', self.pt(s)), setattr(font, 'name', th.font))
        ch.has_legend = len(series) > 1
        if ch.has_legend:
            ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False
            f(ch.legend.font, th.small)
        if title:
            ch.has_title = True
            ch.chart_title.text_frame.text = title
            f(ch.chart_title.text_frame.paragraphs[0].runs[0].font, th.small + 4)
        cols = list(colors or th.accents.values())
        for ser, col in zip(ch.plots[0].series, cols):
            ser.format.fill.solid(); ser.format.fill.fore_color.rgb = th.accent(col)
            ser.invert_if_negative = False
        if labels:
            pl = ch.plots[0]
            pl.has_data_labels = True
            pl.data_labels.number_format = number_format; pl.data_labels.number_format_is_linked = False
            f(pl.data_labels.font, th.small)
            pl.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
        f(ch.category_axis.tick_labels.font, th.small)
        ch.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
        f(ch.value_axis.tick_labels.font, th.small - 2)
        if ymin is not None:
            ch.value_axis.minimum_scale = ymin
        if ymax is not None:
            ch.value_axis.maximum_scale = ymax
        ch.value_axis.major_gridlines.format.line.color.rgb = rgb(th.grid)
        return ch
