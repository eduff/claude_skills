---
name: slides
description: Build editable PowerPoint (.pptx) talks from Python on the user's own template, with a consistent scientific-talk design (assertion titles, bold-lead bullets, figure beside text, cards, headline numbers, equation/model panels, native charts) and matplotlib schematics sized for slides. Use when asked to make, rebuild or restyle a slide deck or talk from a manuscript, results or figures.
---

# slides — scientific talks as editable PowerPoint

Two modules in this directory:

- `slidekit.py`: a `Deck` class over python-pptx. It loads a template (or a
  blank 16:9 deck), keeps its title slide, and adds content slides through
  layout helpers. Every shape is a normal PowerPoint object, so the user can
  finish the deck by hand.
- `figstyle.py`: matplotlib conventions for slide schematics, with large type,
  editable SVG text, a colour-blind-safe palette and `box` / `arrow` / `badge`
  primitives.

`example.py` uses every helper and doubles as a smoke test.

Requirements: `python-pptx`, `Pillow`, `lxml` (and `matplotlib` for figures).

## Setup in a project

```python
import sys; sys.path.insert(0, '/path/to/claude_skills/slides')
from slidekit import Deck, Theme, B, N
from figstyle import use_talk_style, PALETTE, box, arrow, badge, save
```

Put the build script next to the talk (e.g. `talks/<event>/build_deck.py`),
with its own `figures/` folder. Commit the script, so the deck can always be
rebuilt.

## Building a deck

```python
deck = Deck('template.pptx', layout='Title & Bullets', keep_slides=1)   # or Deck() for blank 16:9
deck.retitle(deck.prs.slides[0], {'Old title prefix': 'New talk title',
                                  'Old subtitle prefix': 'New subtitle'})
L, TOP, R, BOT, d = deck.L, deck.TOP, deck.R, deck.BOT, deck.d          # content frame, inches

s = deck.new('APOE shifts the age curve without changing its slope',
             notes='Numbers, caveats and sources go here, not on the slide.')
deck.fig_right(s, 'figures/age.png', [
    [B('Age dominates: '), N('incidence doubles every ~4.4 years')],
    [B('ε4 shifts the level: '), N('2.75× in ε3/ε4, 8.1× in ε4/ε4')],
])
deck.save('talk.pptx')
```

| Helper | Use for |
|---|---|
| `new(title, notes)` | a content slide on the chosen layout |
| `text` / `bullets` | text boxes; paragraphs are strings or lists of `B()` / `N()` runs |
| `fig_right(s, png, bullets, split=)` | the default results slide: claims on the left, figure on the right |
| `fig_full(s, png, caption=)` | a schematic or multi-panel figure across the frame |
| `picture(s, png, x, y, w, h)` | an image fitted into a box with its aspect ratio kept |
| `card` / `cards_row` | 2–3 side-by-side comparisons (aims vs rationale, capability vs risk) |
| `stat` / `stat_row` | headline numbers with one-line labels |
| `equation(s, x, y, w, eq)` | a one-line shaded formula strip |
| `panel(s, x, y, w, h, heading, eq_lines, defs)` | a model, with its equation and every symbol defined |
| `placeholder(s, x, y, w, h, label)` | a dashed box where a figure will be pasted by hand |
| `column_chart(...)` | a small native chart that stays editable, with the data stored in the file |

### Units and canvas size

Positions are in inches against the frame `L, TOP, R, BOT`. Font sizes and
the small offsets passed to `d()` are given for a 15-inch-tall canvas (the
Keynote-export 26.67 × 15 in size), and the deck scales them by
`deck.k = slide_height / 15`. The same script therefore looks the same on a
13.33 × 7.5 in PowerPoint canvas. Use `d(x)` for any fixed offset you add,
and fractions of `deck.width` / `deck.height` for column widths.

### Theme

```python
deck = Deck('template.pptx', layout='Title & Bullets', keep_slides=1,
            theme=Theme(font='Calibri', accents={'main': '#1B5E20', 'alt': '#B71C1C', 'dark': '#263238'}))
```

Accents are referred to by name in `card(..., accent='main')`,
`stat(..., 'alt')` and in run colours (`N('text', color='main')`). Keep
`dark`, because equations use it. To match a template, take its font and its
theme colours.

## Design rules

1. **Each title is an assertion.** Write the finding as a sentence ("Exposures
   multiply with each other at every ε4 dose"), not a topic ("Exposure
   pairs").
2. **No more than 4 bullets, each with a bold lead.** Use
   `[B('Lead phrase: '), N('short continuation')]` and keep each to one line
   if possible. Body text is 26–30 pt on the reference canvas, and nothing is
   smaller than 22.
3. **Put details in the notes.** Exact statistics, caveats, citations and
   methods belong in the speaker notes. The slide keeps one number per claim.
4. **One figure per slide.** Use `fig_right` for results and `fig_full` for
   schematics. Draw talk figures at the size they fill on the slide, with large
   type, rather than shrinking paper figures (see below).
5. **Keep one colour meaning for the whole talk.** Give each accent a fixed
   role (e.g. red = genetic risk, blue = exposure, purple = observed or
   combined), and use the same colours in figures and slide accents.
6. **Use the right structure:** cards for 2–3 parallel ideas, a stat row for
   3–4 headline numbers, a panel for a model, an equation strip for a formula.
7. **Leave space for hand edits.** Use `placeholder` for figures the user will
   supply (published figures, photos). Name shapes (`name=`) so they are easy
   to find in the selection pane.
8. **Background first.** For a general audience, open with 1–2 context slides
   (the problem, the data) before the method.

## Figures for slides

```python
import matplotlib.pyplot as plt
use_talk_style()                          # 18 pt base, editable SVG text
fig, ax = plt.subplots(figsize=(14, 8))   # roughly the slide area the figure will fill, in inches
...
save(fig, 'pathways', outdir='figures')   # writes figures/pathways.png and .svg
```

The `.svg` can be inserted instead of the PNG when the user wants to edit
labels in PowerPoint (Insert → Pictures, then "Convert to Shape").

### Diagrams

Three standard layouts. Each builds its own figure, sized so the type stays in
proportion to the shapes, and shrinks any label that does not fit its shape.
Each returns `fig, ax`, so you can add to it before calling `save`.

| Function | Classic form | Use for |
|---|---|---|
| `flow(steps, shape=, ncols=, side=)` | process / pipeline | study strategy, analysis pipeline, aims. `shape='circle'` (few big steps), `'box'` (wraps onto rows with `ncols`), `'chevron'` (phases, no arrows). `side=[...]` adds a panel for things that apply at every step (bias controls, QC). |
| `cohort_flow(stages, exclusions)` | STROBE / CONSORT participant flow | how the analysed sample was reached: `[('Recruited', n), ...]` with `[(reason, n), ...]` per gap. Warns if a stage's n minus its exclusions does not equal the next. |
| `dag(nodes, edges)` | causal DAG | mechanisms, confounding and bias paths. Nodes are `{key: (x, y, colour, label)}`, and edges take `rad`, `ls='--'` (assumed or unmeasured), `both=True` (correlation), `label=` or `badge=`. |
| `key(ax, x, y, rows)` | badge legend | `(badge, heading, detail)` rows that explain numbered DAG edges. Widen `xlim` and the figure to make room. |

```python
fig, _ = flow([('Extract', 'purple'), ('Clean', 'red'), ('Fit', 'blue'), 'Validate'],
              shape='box', ncols=2, side=['QC at every step'], side_title='Checks')
save(fig, 'pipeline')
```

Rules for diagrams:
- Every node colour follows the talk's colour roles (rule 5), and grey means context.
- Use 3–6 steps in a process figure. More than that belongs on two slides.
- Put a node's description in the slide text or the notes, not inside the node.
- In a DAG, solid black edges are the effects being estimated, coloured edges
  are bias paths, and dashed edges are assumed or unmeasured.
- `cohort_flow` numbers must come from the analysis output. Never type them
  in by hand, and act on the arithmetic warning.

For layouts these don't cover, compose `box` / `arrow` / `badge` directly in
equal-aspect axes, as the layout functions do.

## Check before handing over

- Run the build and open the result. If LibreOffice is available, render a
  PDF to check visually: `soffice --headless --convert-to pdf talk.pptx`.
- Look for text overflowing its box (long bold leads are the usual cause), for
  figures too small to read, and for empty placeholders.
- Report which slides contain placeholders or notes the user must edit.
