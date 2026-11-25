# claude_skills

Reusable Claude Code skills and standalone utility scripts, kept separate
from any single project so they can be shared and version-controlled on
their own.

## Contents

### `cite/` — reference lookup skill
A Claude Code skill that resolves DOIs and reference searches into verified
Crossref metadata and cross-checks them against a personal Zotero library
(read-only). Use it to confirm a citation exists and matches its claim
before putting it in a manuscript, to fetch a DOI from author/year/topic,
or to check whether a paper is already in your Zotero library.

- `cite/SKILL.md` — skill definition and usage.
- `cite/scripts/cite.py` — the implementation (Crossref + Zotero Web API).

Requires `~/.config/zotero/config.json` (`{"user_id": int, "api_key": str}`)
for the Zotero cross-check; Crossref lookups work without it. Optional
`poppler-utils` (`pdftotext`) for PDF text extraction.

### `notebook-utils/` — Jupyter notebook utilities
- `notebook_lighten.py` — strip all cell outputs and execution counts from
  a notebook to reduce file size and speed up remote saving.

### `figutils/` — figure provenance skill
A Claude Code skill + standalone Python library that saves matplotlib figures
and writes a companion `.provenance` file recording the generating script path,
timestamp, and git commit. Enables instant traceability: `cat .provenance/foo.png.provenance`
→ generating script, no grep needed.

- `figutils/SKILL.md` — skill definition: rules Claude must follow (always use
  `save_fig`, always check `.provenance` first, never use heredoc stdin).
- `figutils/figutils.py` — drop-in library; symlink or copy into any project's
  `scripts/` directory. Provides `save_fig`, `read_provenance`, `provenance_path`.

### `writing/` — prose style guide
A reference list of AI writing patterns to avoid, applied when drafting or
editing manuscript text, documentation, commit messages, and replies. Covers
negative parallelism ("it's not X, it's Y"), em-dash overuse, preamble that
announces the point instead of making it, reasoning leaks, invented compound
labels, magic adverbs, signposted conclusions, and about forty others.

- `writing/SKILL.md` — how to apply it, plus a short checklist for quick edits.
- `writing/ai-tropes.md` — the full reference, from
  [tropes.fyi](https://tropes.fyi) by [ossama.is](https://ossama.is).

A project's own `CLAUDE.md` takes precedence where the two conflict: this
guide constrains the shape of the prose, project rules constrain its content.

### `data-compliance/` — restricted-data audit and removal skill
A Claude Code skill + stdlib-only Python toolkit for finding and provably
removing individual-level research data (UK Biobank participant IDs and
similar) from servers, laptops, code and git history, while keeping code and
aggregate results. Scan, inventory, git-history audit, regenerability check,
manifest-based quarantine/delete with a destruction record, and in-place
masking of transcripts, notebooks and shell histories.

- `data-compliance/SKILL.md` — rules, workflow and post-clean-up verification
  checklist (including Claude Code's own transcripts and scratch files).
- `data-compliance/bin/data-compliance` — runs from the clone, no install.
- `data-compliance/README.md` — full command reference; `tests/` — unittest suite.

### `utils/` — misc helpers
- `tech.py` — print the largest objects in the current Python namespace by
  in-memory size (quick memory-profiling snippet).

## Installing a skill into Claude Code

Skills are discovered from `~/.claude/skills/<name>/SKILL.md`. To use the
`cite` skill, either copy `cite/SKILL.md` into `~/.claude/skills/cite/` and
point its "Script location" at the absolute path of `cite/scripts/cite.py`
in this repo, or symlink it.

## License

Personal tooling — add a license before publishing if needed.
