---
name: cite
description: Resolve a DOI or reference search into verified Crossref metadata and cross-check against the user's Zotero library. Use this skill whenever you need to (a) confirm that a citation you are about to use in a manuscript actually exists and is the paper you think it is, (b) fetch a DOI given author/year/topic, or (c) check whether a paper is already in the user's Zotero library before recommending they download it.
---

# /cite — Reference lookup

Wraps `scripts/cite.py`, which uses the Crossref public API for metadata and the Zotero Web API (read-only) for library cross-check. Credentials live in `~/.config/zotero/config.json`.

## Core invocations

Look up a DOI and cross-check against the Zotero library:

```bash
python3 <repo>/scripts/cite.py --doi 10.1001/archneurol.2010.201
```

**Look up by PMID** (resolves to DOI via NCBI, then runs full pipeline):

```bash
python3 <repo>/scripts/cite.py --pmid 41290621
python3 <repo>/scripts/cite.py --pmid 41290621 --full
```

Search Crossref when you only have author/year/topic:

```bash
python3 <repo>/scripts/cite.py --search "Jun 2010 meta-analysis CR1 CLU PICALM APOE interactions"
```

Emit BibTeX for an in-draft reference list:

```bash
python3 <repo>/scripts/cite.py --doi <DOI> --bib
```

Include Zotero notes + the text of an attached PDF *or* HTML snapshot:

```bash
python3 <repo>/scripts/cite.py --doi <DOI> --full
python3 <repo>/scripts/cite.py --doi <DOI> --full --text-chars 0   # no truncation
```

The `--full` path tries the Zotero `/fulltext` index first. When that 404s
(common — Zotero only indexes some files, and the index lags syncing),
it falls back to downloading the raw attachment via `/file`:

- **Snapshot (HTML in a ZIP)** — unzipped and tag-stripped to plain text.
  This is the most useful path when the user's storage budget rules out
  PDFs: web snapshots saved via the Zotero connector are full-text
  reachable and give the entire article body (~30–40k chars typical).
- **Raw HTML** — tag-stripped directly.
- **PDF** — extracted via `pdftotext` if `poppler-utils` is on PATH;
  otherwise reports a clear error so the user knows to install it.

### When attachment text is unavailable: broader search strategy

If `--full` reports **"file-local-only"** it means the attachment exists in the user's
Zotero desktop app but has **not been synced to Zotero cloud storage** — the API cannot
serve it. The script automatically:
1. Tries **all attachments** on the matched item (PDFs first, then snapshots) rather than
   stopping at the first failure.
2. Falls back through **all duplicate library records** for the same DOI — a duplicate
   added at a different time may have a synced attachment even when the primary does not.
3. Tries the **Zotero local HTTP server** (`localhost:23119`) as a last resort (only works
   when Zotero is running on the same machine).

If all attempts fail, tell the user:
> "The attachment is stored locally in your Zotero desktop app but hasn't been synced to
> Zotero cloud. To make it accessible: in Zotero desktop, right-click the attachment →
> 'Manage Attachments' → ensure cloud sync is on, **or** export/drag the PDF into the
> workspace so I can read it directly."

## How to use this when drafting

1. Before using "Author YYYY" in manuscript text, run `--search` or `--doi` to verify the paper exists and its abstract matches your claim.
2. Quote back the DOI to the user so they can open it in a browser and save to Zotero.
3. After they have saved it, re-run `--doi` to pull their notes/tags and confirm the paper is now in the library.
4. Do **not** invent DOIs. If `--search` does not return a clear match, report this explicitly rather than guessing.

## Configuration

Create `~/.config/zotero/config.json`:

```json
{"user_id": 1234567, "api_key": "your_zotero_read_only_api_key"}
```

`user_id` is your numeric Zotero user ID (Settings → Feeds/API). `api_key`
is a read-only key created at https://www.zotero.org/settings/keys.
Without this config, Crossref lookups still work; only the Zotero
cross-check is disabled.

Optional: `poppler-utils` on PATH (`pdftotext`) for PDF text extraction.

## Script location

This repo: `cite/scripts/cite.py`. Run it via its absolute path from any
project, e.g. `python3 /path/to/claude_skills/cite/scripts/cite.py --doi <DOI>`.
