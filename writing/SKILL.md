# writing — prose style guide

Apply when drafting or editing any prose: manuscript text, documentation,
commit messages, and replies in conversation. The reference list of patterns
to avoid is `ai-tropes.md` in this directory, reproduced from
[tropes.fyi](https://tropes.fyi) by [ossama.is](https://ossama.is).

Read `ai-tropes.md` before a substantial writing task. For short edits the
checklist below covers the patterns that recur most.

---

## Checklist

Run over a draft before delivering it.

1. Negative parallelism. Search the draft for "not X, it's Y" and
   "the question isn't ... it's". Once in a piece can work; twice is a tic.
2. Em dashes. Count them. More than two or three in a piece means rewriting
   some as commas, full stops, or parentheses.
3. Short fragments as paragraphs. Sentences under about six words standing
   alone are manufactured emphasis. Join them to what they qualify.
4. Preamble. Delete any sentence that announces what the next sentence will
   do, including "Two constraints shape the design" and "The more important
   point is". State the point instead.
5. Reasoning leak. Delete narration of your own process: "what this changes
   is worth being precise about", "I want to be exact about my role here".
6. Counting. Do not state how many items follow. Let the list be its length.
7. Invented labels. Compound nouns such as "supervision paradox" or
   "acceleration trap" name a thing in place of arguing for it. Say what
   happens instead.
8. Rule of three. Three parallel clauses is a figure of speech; three
   tricolons in succession is a stuck generator.
9. Magic adverbs: quietly, deeply, fundamentally, remarkably, arguably.
   Delete or replace with the specific claim.
10. The tie-back. Once the point is delivered, stop. Do not restate it
    against the original question.
11. Vague attribution: "experts argue", "industry reports suggest". Name the
    source or drop the claim.
12. Ornate nouns: tapestry, landscape, paradigm, ecosystem, load-bearing,
    gated. Use the plain word.
13. Bold-first bullets. A bolded keyword opening every list item is a
    formatting tell; use it only where the keyword is genuinely a label.
14. Signposted conclusions: "in conclusion", "to sum up", "in summary".
15. Title case headings. Sentence case only.
16. Unicode decoration: arrows and curly quotes that a keyboard does not
    produce.
17. Overlong, logically nested sentences. The counterpart of trope 3, and not
    on the tropes.fyi list: a sentence carrying two or three dependent claims
    through colons, semicolons, em dashes and subordinate clauses, so the
    reader must hold several conditions open to reach the verb. Symptoms are
    a sentence over about 40 words, more than one semicolon or colon, or a
    parenthetical inside a subordinate clause. Split it at the logical joint
    and let each claim have its own sentence. This survives most trope passes
    because each individual clause reads well.

---

## Interaction with project style rules

A project's own `CLAUDE.md` may impose stricter or different rules, for
example on hedging, paragraph length, or how many numbers a claim may carry.
Those take precedence where they conflict. This guide constrains the shape of
the prose; the project rules constrain its content.

---

## Applying it to existing text

When asked to go through a document for these patterns, work paragraph by
paragraph and report what changed rather than silently rewriting. Some of
these patterns are legitimate in small numbers, and the author is better
placed to judge which instances earn their place.
