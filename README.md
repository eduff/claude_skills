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
