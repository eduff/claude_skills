---
name: data-compliance
description: Audit and safely remove restricted individual-level research data (UK Biobank participant IDs and similar) from a server, laptop, codebase or git history, keeping code and aggregate results. Use when asked to find where participant data lives, check whether it has leaked into git or Claude/IPython transcripts, plan or carry out deletion at the end of a data-access period, mask identifiers in files that should be kept, or produce a destruction record.
---

# data-compliance — find, remove and certify removal of participant data

Wraps `bin/data-compliance` in this directory: a stdlib-only Python toolkit
(Python 3.8+; `pandas`/`pyarrow` optional for pickle/parquet columns). No
install needed. Full command reference: `README.md`. macOS specifics:
`doc/MACOS_DELETION.md`.

```bash
DC=<path to this directory>/bin/data-compliance
$DC --help
```

## Hard rules

1. **No deletion, quarantine, masking or history rewrite without explicit
   approval for that specific operation.** Approval for one does not carry to
   the next, even later in the same session. Present the plan, wait.
2. **Dry run first, every time.** `purge execute`, `redact` and
   `rewrite-history` all default to a dry run; add `--execute` only after the
   user has seen the dry-run output.
3. **Never print identifier values.** Report counts and contexts with the
   number masked. Audit output must itself be shareable. Prefer `scan`
   (read-only) to `redact` when you only need to check.
4. **Never run `git reset --hard`, `git checkout -- .`, `git clean` or
   `git stash` in a repo with uncommitted changes you did not create.**
   Unstaged edits are unrecoverable. Test git behaviour in a throwaway
   `git init` directory.
5. **Rules only move in the conservative direction.** Never widen a
   classification rule to make a file purgeable; never loosen the provenance
   matcher (a false "regenerable" makes an irreplaceable file look safe).
   Suppressing a *clear* false positive is fine if the suppression is visible
   and explained (e.g. a `.data-compliance.json` `allow_values` entry with a
   note).
6. **Check regenerability before purging.** If a large derived file has no
   generator in code, say so — the user may want to keep or rebuild it first.

## Workflow

```bash
# 1. Survey (read-only)
$DC --profile ukb inventory /data /code --progress      # KEEP / REVIEW / PURGE proposals
$DC --profile ukb scan /code --progress                 # identifiers in files
$DC --profile ukb git-audit /path/to/repo               # tracked + full history + remotes
$DC --profile ukb paths /code --filenames               # server paths, credentials
$DC --profile ukb report /data /code -o COMPLIANCE_AUDIT.md

# 2. Regenerability
$DC --profile ukb provenance /path/to/project --min-mb 100

# 3. Plan -> manifest (+ readable .md). Touches nothing. User reviews it.
$DC --profile ukb purge plan /path/to/project -m purge.json

# 4. Quarantine (reversible), dry run then execute
$DC purge execute -m purge.json --mode quarantine --quarantine /quarantine
$DC purge execute -m purge.json --mode quarantine --quarantine /quarantine --execute

# 5. After a holding period: delete. Fingerprints are re-verified first.
$DC purge execute -m <manifest of quarantined files> --mode delete --log purge.jsonl --execute

# 6. Destruction record from the append-only log
$DC purge certificate --log purge.jsonl > DESTRUCTION_RECORD.md
```

Keep one append-only log (`--log`) for the whole clean-up so the certificate
covers every operation. Keep manifests next to it.

### Files to keep but clean: `redact`

Transcripts, notebooks and shell/IPython histories can be masked in place
instead of deleted. Every in-range ID match becomes `REDACTED_ID`; JSONL
structure, notebook validity and SQLite history are preserved.

```bash
$DC --profile ukb redact ~/.claude ~/.bash_history ~/.ipython        # dry run, counts only
$DC --profile ukb redact ~/.claude ~/.bash_history ~/.ipython --execute
```

Files modified within `--min-age-min` (default 10) minutes are refused because
they may be live. Pass `--min-age-min 0` only when **no Claude Code session or
shell is running** (close every session, `claude daemon stop`). A session
cannot safely mask its own transcript — it appends to it on every turn — and
**resuming** a session rewrites its context into the transcript again, so mask
after the last session closes and start a fresh one afterwards.

### Ad-hoc manifests

For a handful of files found after planning (e.g. leftovers from a
verification pass), build a manifest in Python rather than deleting by hand,
so the deletion is fingerprinted and logged:

```python
from pathlib import Path
import time, socket, getpass
from datacompliance.purge import Manifest, MANIFEST_VERSION, fingerprint
m = Manifest(version=MANIFEST_VERSION, created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
             host=socket.gethostname(), user=getpass.getuser(), profile='ukb',
             roots=[...], notes='why')
for p in files:
    m.entries.append({'path': str(p), 'category': '...', 'rule': '...',
                      'reason': '...', 'fingerprint': fingerprint(p)})
m.save(Path('extra.json'))
```

Run with `PYTHONPATH=<this dir>/src`, then `purge execute -m extra.json --mode delete --log purge.jsonl` (dry run, then `--execute`).

## End-of-access clean-up: order of work

Use this sequence when a data-access period is ending and a whole machine has
to be cleared. Each phase depends on the one before; do not reorder.

1. **Set scope and ground rules.** Agree with the data owner what may stay
   (code, documents, figures, aggregate results) and what must go (every file
   with one row per participant, raw or derived, with or without IDs). List
   every root: home, data and scratch disks, other users' or shared folders,
   backups, repo mirrors. Resolve symlinked mounts so files are not counted
   twice. Start a living state document and keep it updated. It records
   decisions and status so that a later session continues the work instead of
   re-planning it.

2. **Finish the analyses that need individual-level data.** Re-runs,
   sensitivity analyses and corrections are impossible afterwards. Ask the
   owner explicitly what is still pending.

3. **Preserve what must outlive the data.**
   - Trace every figure, table and in-text number in the manuscript or report
     back to its output files, and copy the aggregate ones into a separate kept
     package. Give each file a manifest row saying why it is aggregate.
   - Build aggregate tables that allow the main models to be refitted later:
     person-time and events by exposure × level × age × sex × key strata,
     sufficient statistics for linear models, and cohort benchmark summaries
     for checking a future rebuild. Blank small cells if the data provider
     requires it.
   - Document how every analysis variable was derived from source fields, and
     record known issues found along the way.
   - **Validate:** refit the main results from the aggregate package and
     compare them with the published outputs. Do not proceed until they agree
     within a small fraction of a standard error.

4. **Clean code and git.**
   - Clear notebook outputs that contain IDs, and mask IDs typed into code
     cells.
   - If repository history holds identifiers, start a fresh repository from the
     cleaned tree rather than rewriting history in place. Audit it with this
     tool and with the provider's official checker, classifying every hit.
     Install the pre-commit guard.
   - The owner deletes old remotes. Old `.git` directories and mirrors go on
     the purge list.

5. **Plan the deletion.**
   - Run `purge plan` over all roots. Rules give PURGE, and everything
     undecided is REVIEW.
   - Content-check the REVIEW set and give every file a recommendation:
     DELETE, REDACT (mask and keep), KEEP or CHECK (the human decides).
   - Have a second reviewer, or a separate agent, check the plan against the
     preserved package: every deleted input to a kept result must have its
     aggregate output preserved.
   - Log each decision with its reason. Note beside any script that the
     deletion leaves unrunnable.
   - Credentials found in old snapshots: delete them, and rotate the keys.

6. **Quarantine, then re-check.** Move PURGE and DELETE files to quarantine.
   This step is reversible. Confirm that the kept package and paper checks
   still run without the data.

7. **Mask the archival files** (transcripts, notebooks, histories) with
   `redact`, after closing every Claude Code session. Delete outright any
   transcript too full of data to be worth masking.

8. **Delete.** After the holding period the owner chooses, run `purge execute
   --mode delete` on the quarantine. It re-verifies fingerprints first.
   Generate the destruction record.

9. **Verify and close out.** Re-scan the places listed in the verification
   checklist below, delete any leftovers through a logged manifest, and
   regenerate the destruction record. Update the state document, confirm the
   keys were rotated, and leave a short resume note listing anything still
   open.

## Verification checklist (after a clean-up)

A plan only covers the roots it was given. Before declaring done, re-scan
these, which are easy to miss:

- **Claude Code state:** `~/.claude/projects/*/*.jsonl` and their
  `subagents/`, `~/.claude/file-history/` (extensionless `<hash>@vN` undo
  snapshots — `redact` skips unsupported types, so these must be scanned and
  deleted, not masked), `~/.claude/todos`, `~/.claude/paste-cache`,
  `~/.claude/backups`, any relocated `.claude` directory.
- **Session scratchpads and task output:** `/tmp/claude-<uid>/` — scratch CSVs
  with an `eid` column end up here and are outside any project root.
- **IDE agent data:** e.g. `~/.vscode-server/data/agentSessionData/`.
- **Shell and REPL history:** `~/.bash_history`, `~/.zsh_history`,
  `~/.python_history`, `~/.ipython/profile_*/history.sqlite`, `~/.Rhistory`.
- **Git:** every repo's history and remotes (`git-audit`), not just the
  working tree; bare mirrors and backup clones.
- **Archives:** `.tar.gz`/`.zip` in home and scratch, which content scans
  cannot see into.

Read-only re-check, counts only:

```bash
$DC --profile ukb scan ~/.claude /tmp/claude-$(id -u) ~/.bash_history --progress
```

## Triage of scanner hits

The ID pattern is any 7-digit run in range, so false positives are common.
Classify before acting, showing context with the digits masked:

- **Inside hex strings** (hashes, digests, UUIDs, Jupyter cell ids, file-history
  names like `2f7a7c5d…@v3`): not IDs.
- **Config / version / statistics numbers** (sample sizes, p-values, app
  settings): not IDs; `scan` already down-weights statistical context.
- **URLs** (Stack Overflow answer ids, DOIs): not IDs; spared by `redact`.
- **Tables with an `eid`-like column, ID-keyed text, filenames
  `<id>_<field>_<instance>`**: real — treat as data.

The `ukb` profile's ID regex is byte-identical to UK Biobank's own
`UKB-Git-Audit-Tool` (asserted in tests). That tool is the authoritative check
for repositories; it cannot audit bare repos and counts notebook cell ids, so
classify its hits too.

## Extending

- Rules: `profiles/ukb.json` (extends `generic.json`); first match wins,
  nothing falls through to KEEP. Data-file KEEP rules should set
  `"content_check": true`.
- Per-project overrides: `.data-compliance.json` at the project root (merged
  over the profile; lists accumulate). This directory's own file allowlists
  the synthetic/test values used in the code and tests.
- Tests: `python3 -m unittest discover -s tests`.
