# data-compliance

Audit and safely remove restricted individual-level research data — UK Biobank
participant IDs and equivalents — from a machine, a codebase, and a git history.

Written for the situation where a study is winding down and the data has to go,
but the code and the aggregate results have to stay, and you need to be able to
say afterwards exactly what was removed.

- **Stdlib only.** Python 3.8+. `pandas` and `pyarrow` are optional and only
  used to read pickle/parquet columns when they happen to be installed.
- **No install required.** `git clone` and run `bin/data-compliance`.
- **Nothing is deleted without a reviewed manifest and an explicit flag.**

## The four questions

| Question | Command |
|---|---|
| Where is participant data? | `scan`, `inventory` |
| Has it escaped? | `git-audit` — tracked files, **full history**, remote push state |
| Can I safely delete it? | `provenance` — is it regenerable from code? |
| Remove it, provably | `purge plan` → `purge execute` → `purge certificate` |

## Quick start

```bash
git clone <repo> ~/code/data-compliance
DC=~/code/data-compliance/bin/data-compliance

$DC --profile ukb report /path/to/project -o COMPLIANCE_AUDIT.md
```

## Why the extra machinery

**A filesystem scan is not an exposure audit.** A notebook can be spotlessly
clean in the working tree and still carry 80,000 participant IDs in every
historical commit, on a remote you pushed to months ago. `git-audit` scans every
blob reachable from every ref, not just `HEAD`.

**Deleting data is only safe if you can rebuild it.** `provenance` finds the
statements that write each file and the statements that read it, then issues a
verdict. It is deliberately pessimistic:

| Verdict | Meaning |
|---|---|
| `REGENERABLE` | A write statement exists in tracked, non-notebook code |
| `CHAIN_AT_RISK` | Its generator reads inputs that are themselves being removed |
| `GENERATOR_UNTRACKED` | Only notebook or untracked code writes it |
| `GENERATOR_COMMENTED_OUT` | The only write statement is commented out |
| `NO_GENERATOR` | Read in N places, written nowhere — cannot be rebuilt |
| `ORPHAN` | No code refers to it at all |

Commented-out writes do not count as generators, and a bare filename stem is
never an exact match. A false `REGENERABLE` is how an irreplaceable dataset gets
deleted, so the matcher errs the other way.

**Removal is two phases that cannot be collapsed.** `purge plan` fingerprints
every target (size + mtime + head/tail SHA-256) and writes a manifest plus a
readable `.md`. `purge execute` re-verifies each fingerprint before acting and
skips anything that changed. It quarantines by default rather than unlinking,
confines every target to a root recorded in the manifest, refuses guarded and
shallow paths, never follows symlinks, and appends a JSONL record of every
action — from which `purge certificate` renders a destruction record.

## Commands

```
scan PATH...             participant identifiers, with confidence levels
                         (--progress for a live counter; scan reads file
                          contents, so it is the slow one)
paths PATH...            server-identifying paths and credentials in source
inventory PATH...        classify every file: KEEP / REVIEW / PURGE
provenance PATH...       regenerability verdict per purge target
git-audit [REPO]         tracked + historical + remote exposure
report PATH... -o FILE   all of the above as one markdown document
notebooks MODE FILE...   audit | clean (flagged cells only) | strip (all outputs)
purge plan|execute|certificate
install-hook [REPO]      pre-commit guard against committing identifiers
rewrite-history REPO     guarded git-filter-repo wrapper (backs up, never pushes)
```

Exit codes: `0` clean, `1` findings, `2` usage/error — so `scan --quiet` works
in a hook or CI job.

## Profiles

Rules live in `src/datacompliance/profiles/*.json`, not in code, so decisions are reviewable in a
diff. `ukb.json` extends `generic.json`.

A profile defines the identifier pattern and range, the context words that raise
or lower confidence, skip lists, server-path and credential patterns, sensitive
filename rules, and the ordered classification rules that assign each file a
category and disposition. First matching rule wins; **nothing falls through to
`KEEP`** — unmatched files become `REVIEW`.

Drop a `.data-compliance.json` at a project root to select a profile and layer
project-specific rules on top. List-valued keys accumulate; scalars override.

## False positives

Scientific output is full of seven-digit numbers that are not participant IDs.
Two guards do most of the work: a large `negative_context` vocabulary
(`p=`, `n_events`, `exp(coef)`, `dtype`, …) around the candidate, and the
decimal-tail rule — `1234567.85` is never an ID, while `1234567.0` counts only
when nearby text names an identifier, because pandas renders an integer ID
column as float whenever NaNs are present.

CSV, parquet and pickle files are scanned by *column semantics* instead: if a
column is named `eid`/`f.eid`/`participant_id`, its in-range values are counted
directly and reported HIGH.

## This repository self-reports

Scanning this repo flags a handful of matches. They are all deliberate: the
identifier examples published in UK Biobank's own tool documentation
(`1234567`, `patient5082392`, `3081375_20215_0_0.zip`), plus a Stack Overflow
URL used to illustrate a false-positive guard. No real identifier belongs in
this repository — if one appears, it is a bug, so run `scan` before publishing:

```bash
bin/data-compliance --profile ukb scan .
```

## Deleting on macOS

Removing a file on a Mac leaves copies in the Trash, APFS local snapshots
(taken hourly whether or not a backup disk is attached), Time Machine, iCloud
Recently Deleted, the Versions store and Quick Look caches. See
[doc/MACOS_DELETION.md](doc/MACOS_DELETION.md) for the order to work in — stop
backups *before* deleting, or the next backup recaptures what you just removed.

## Safety properties

- Scanners print counts and redacted contexts, never identifier values, so the
  audit output is itself shareable.
- Credential matches are recorded as `<pattern redacted>`, never the secret.
- `purge execute` defaults to a dry run; `--execute` is required to act.
- `rewrite-history` mirror-clones the repo before rewriting and never pushes.
- The pre-commit hook scans staged *content*, so a dirty working tree cannot
  mask what is actually being committed.

## License

Add one before publishing.
