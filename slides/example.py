#!/usr/bin/env python3
"""Demo deck using every slidekit layout. Doubles as a smoke test.

    python3 example.py                         # blank 16:9 deck
    python3 example.py my_template.pptx 'Title & Bullets' 1
                                               # template, content layout, slides to keep
Output: example_deck.pptx (and figures/demo_*.png)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, 'figures')


def demo_figures():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from figstyle import PALETTE, arrow, badge, box, save, use_talk_style
    use_talk_style()
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.set_xlim(0, 10); ax.set_ylim(0, 7); ax.set_axis_off()
    box(ax, 0.5, 4.5, 3.5, 1.5, 'Factor A', PALETTE['orange'], tc='white', weight='bold')
    box(ax, 0.5, 1.0, 3.5, 1.5, 'Factor B', PALETTE['blue'], tc='white', weight='bold')
    box(ax, 6.0, 2.75, 3.5, 1.5, 'Outcome', PALETTE['purple'], tc='white', weight='bold')
    arrow(ax, (4.0, 5.25), (6.0, 3.8), rad=-0.1)
    arrow(ax, (4.0, 1.75), (6.0, 3.2), rad=0.1)
    badge(ax, 5.0, 5.0, 1); badge(ax, 5.0, 2.0, 2)
    save(fig, 'demo_schematic', FIG)

    fig, ax = plt.subplots(figsize=(10, 7))
    x = range(40, 85, 5)
    ax.plot(x, [0.5 * 2 ** ((a - 40) / 5) for a in x], color=PALETTE['red'], lw=3, label='Group 1')
    ax.plot(x, [0.2 * 2 ** ((a - 40) / 5) for a in x], color=PALETTE['blue'], lw=3, label='Group 2')
    ax.set_yscale('log'); ax.set_xlabel('Age'); ax.set_ylabel('Rate per 1,000 person-years')
    ax.legend(frameon=False)
    save(fig, 'demo_plot', FIG)

    # Classic diagram layouts: process flow, STROBE participant flow, causal DAG
    from figstyle import INK, cohort_flow, dag, flow, key
    fig, _ = flow([('Build\nmodel', 'purple'), ('Anchor on\nknown effect', 'red'), ('Map\ninteractions', 'blue'),
                   ('Validate', 'green'), ('Impact', 'grey')],
                  side=['reverse causation', 'ascertainment', 'competing death', 'age-varying effects'],
                  side_title='Bias controls')
    save(fig, 'demo_flow', FIG)

    fig, _ = cohort_flow([('Recruited', 10000), ('Eligible', 9500), ('Analysed', 8950)],
                         [[('withdrew consent', 100), ('no genotype data', 400)],
                          [('prevalent disease', 300), ('missing covariates', 250)]])
    save(fig, 'demo_cohort', FIG)

    nodes = {'A': (0, 6, 'red', 'Factor A'), 'B': (0, 0, 'blue', 'Factor B'),
             'Y': (8, 3, '#333333', 'Outcome'), 'H': (4, -3, '#f2dede', 'Healthcare\ncontact', INK),
             'D': (8, 8.5, '#f2dede', 'Death', INK)}
    fig, ax = dag(nodes, [('A', 'Y'), ('B', 'Y'),
                          ('H', 'B', {'color': 'alert', 'badge': 1, 'rad': -0.2}),
                          ('H', 'Y', {'color': 'alert', 'badge': 1}),
                          ('A', 'D', {'color': 'alert', 'badge': 2, 'rad': -0.15}),
                          ('D', 'Y', {'color': 'alert', 'badge': 2}),
                          ('A', 'B', {'both': True, 'ls': '--', 'label': 'r'})])
    x0, x1 = ax.get_xlim()           # widen the canvas to make room for the badge key
    fig.set_size_inches(fig.get_size_inches()[0] * (x1 - x0 + 8) / (x1 - x0), fig.get_size_inches()[1])
    ax.set_xlim(x0, x1 + 8)
    key(ax, x1 + 0.5, 8.5, [(1, 'Ascertainment', 'negative-control diagnoses'),
                            (2, 'Competing death', 'refit with death as outcome')])
    save(fig, 'demo_dag', FIG)


def build(template=None, layout=None, keep=0, out='example_deck.pptx'):
    from slidekit import B, Deck, N
    if not os.path.exists(os.path.join(FIG, 'demo_dag.png')):
        demo_figures()
    deck = Deck(template, layout=layout, keep_slides=keep)
    F = lambda n: os.path.join(FIG, n + '.png')
    L, TOP, BOT, W, d = deck.L, deck.TOP, deck.BOT, deck.width, deck.d

    s = deck.new('Rates double every five years in both groups',
                 'Speaker notes hold the detail and the exact numbers; the slide holds the claim.')
    deck.fig_right(s, F('demo_plot'), [
        [B('Age dominates: '), N('doubling every 5 years')],
        [B('Group 1 is higher '), N('at every age (2.5×)')],
        [B('Parallel lines: '), N('the effects multiply')],
    ])

    s = deck.new('Two factors can combine in two ways')
    deck.fig_full(s, F('demo_schematic'), caption='Caption in muted text under a full-width figure')

    s = deck.new('Five steps, with bias controls at every one')
    deck.fig_full(s, F('demo_flow'))

    s = deck.new('8,950 of 10,000 participants entered the analysis')
    deck.fig_right(s, F('demo_cohort'), [
        [B('Exclusions: '), N('consent, genotype, prevalent disease')],
        [B('Each box: '), N('n and reason, STROBE style')],
    ], split=0.45 * W)

    s = deck.new('Two bias paths can mimic an interaction')
    deck.fig_full(s, F('demo_dag'))

    s = deck.new('Headline numbers and cards')
    deck.stat_row(s, TOP, [('14', 'risk factors', 'blue'), ('≈45%', 'potentially preventable', 'purple'),
                           ('2.8×', 'per allele', 'red')], size=60)
    deck.cards_row(s, TOP + d(3.2), BOT - TOP - d(3.2), [
        ('Capability', ['Point one', 'Point two'], 'green'),
        ('Verification', ['Point one', [B('Bold lead: '), N('detail')]], 'red'),
        ('Open questions', ['Point one'], 'purple'),
    ], size=26)

    s = deck.new('Model panel, equation strip and a placeholder')
    pw = 0.45 * W
    deck.panel(s, L, TOP, pw, BOT - TOP - d(1.5), 'The model', ['h(t) = h₀(t) · exp(β·X)'], [
        [B('h(t): '), N('hazard at time t')],
        [B('β: '), N('log hazard ratio')]])
    deck.equation(s, L, BOT - d(1.1), W, 'RERI = R₁₁ − R₁₀ − R₀₁ + 1   ·   Int_mult = R₁₁ / (R₁₀·R₀₁)', size=26)
    deck.placeholder(s, L + pw + d(0.5), TOP, W - pw - d(0.5), BOT - TOP - d(1.5),
                     'Insert: published figure', 'paste here and delete this box')

    s = deck.new('Native charts stay editable')
    deck.bullets(s, L, TOP + d(0.2), 0.4 * W, BOT - TOP, [
        [B('In-sample: '), N('looks good')], [B('Held out: '), N('below zero')]], size=30)
    deck.column_chart(s, L + 0.45 * W, TOP, 0.55 * W, BOT - TOP, ['Model A', 'Model B'],
                      {'In-sample R²': (0.21, 0.44), 'Held-out R²': (-0.21, -1.81)},
                      title='Fit vs validation', colors=['purple', 'red'], ymin=-2, ymax=0.6)

    deck.save(os.path.join(HERE, out))
    print('wrote', out, len(deck.prs.slides), 'slides on', f'{deck.W:.2f} x {deck.H:.2f} in')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--figures']:  # figures only (e.g. from a Python with matplotlib but no python-pptx)
        demo_figures(); sys.exit()
    build(a[0] if a else None, a[1] if len(a) > 1 else None, int(a[2]) if len(a) > 2 else 0)
