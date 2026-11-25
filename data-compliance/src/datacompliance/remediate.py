"""Non-destructive remediation: notebook output cleaning, pre-commit hook,
and a guarded wrapper around git history rewriting.

The history rewrite is the only operation here that is not reversible in place.
It refuses to run without an explicit ``--execute`` flag *and* a verified
backup, and it never pushes — publishing the rewrite stays a manual step.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .profile import Profile
from .redact import build_callback, build_cell_id_callback
from .scan import notebook_output_text, scan_text


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

@dataclass
class NotebookResult:
    path: Path
    cleared: int = 0
    preserved: int = 0
    out_path: Optional[Path] = None
    error: str = ""


def clean_notebook(path: Path, profile: Profile, mode: str = "flagged",
                   out_path: Optional[Path] = None,
                   dry_run: bool = False) -> NotebookResult:
    """Clear notebook outputs.

    ``mode='flagged'`` clears only cells whose outputs contain identifiers,
    preserving the rest — the notebook stays useful as documentation.
    ``mode='all'`` clears every output.
    """
    res = NotebookResult(path=path)
    try:
        nb = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        return res

    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code" or not cell.get("outputs"):
            continue
        if mode == "all":
            hit = True
        else:
            blob = "".join(notebook_output_text(cell))
            hit = scan_text(blob, profile).total > 0 if blob else False
        if hit:
            cell["outputs"] = []
            cell["execution_count"] = None
            res.cleared += 1
        else:
            res.preserved += 1

    target = Path(out_path) if out_path else path
    res.out_path = target
    if not dry_run and res.cleared:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(nb, indent=1) + "\n")
    return res


# ---------------------------------------------------------------------------
# Pre-commit hook
# ---------------------------------------------------------------------------

HOOK_TEMPLATE = '''#!/usr/bin/env bash
# Installed by data-compliance. Blocks commits containing participant
# identifiers. Bypass with `git commit --no-verify` only when you are certain.
set -euo pipefail

DATACOMP={datacomp!r}
PROFILE={profile!r}

staged=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$staged" ] && exit 0

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The scan runs against a copy of the staged content, so carry the project
# config across; without it the hook applies different rules from the working
# tree and rejects commits a plain scan accepts.
root=$(git rev-parse --show-toplevel)
for cfg in .data-compliance.json .data-compliance.local.json; do
  [ -f "$root/$cfg" ] && cp "$root/$cfg" "$tmp/$cfg"
done

# Scan the staged *content*, not the working tree, so a dirty tree cannot mask
# what is actually being committed.
while IFS= read -r f; do
  [ -z "$f" ] && continue
  mkdir -p "$tmp/$(dirname "$f")"
  git show ":$f" > "$tmp/$f" 2>/dev/null || true
done <<< "$staged"

if ! "$DATACOMP" --profile "$PROFILE" scan --quiet "$tmp"; then
  echo
  echo "COMMIT BLOCKED: participant identifiers found in staged content."
  echo "Run:  $DATACOMP --profile $PROFILE scan $tmp   to see details"
  echo "      $DATACOMP notebooks clean <file>   to clear notebook outputs"
  exit 1
fi
'''


def install_hook(repo: Path, datacomp: Path, profile: str = "generic",
                 force: bool = False) -> Path:
    hooks = Path(subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        stdout=subprocess.PIPE, check=True).stdout.decode().strip())
    if not hooks.is_absolute():
        hooks = repo / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    dest = hooks / "pre-commit"
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} exists; pass --force to replace "
                              f"(back it up first if it is yours)")
    dest.write_text(HOOK_TEMPLATE.format(datacomp=str(datacomp), profile=profile))
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return dest


# ---------------------------------------------------------------------------
# History rewrite
# ---------------------------------------------------------------------------

@dataclass
class RewritePlan:
    repo: Path
    paths_to_purge: List[str]
    paths_to_strip_outputs: List[str]
    redact_identifiers: bool = False
    profile: object = None
    normalise_cell_ids: bool = False
    backup: Optional[Path] = None
    tool: str = ""
    commands: List[str] = None


def find_filter_repo() -> Optional[str]:
    """Locate git-filter-repo, as a binary or an importable module."""
    exe = shutil.which("git-filter-repo")
    if exe:
        return exe
    try:
        import git_filter_repo  # noqa: F401
        return "python-module"
    except ImportError:
        return None


def backup_repo(repo: Path, dest_dir: Path) -> Path:
    """Mirror-clone the repo so the rewrite is reversible."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{repo.name}-pre-rewrite-{stamp}.git"
    subprocess.run(["git", "clone", "--mirror", str(repo), str(dest)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return dest


def plan_rewrite(repo: Path, purge_paths: List[str],
                 strip_output_paths: List[str],
                 redact_identifiers: bool = False,
                 normalise_cell_ids: bool = False,
                 profile=None) -> RewritePlan:
    """Describe the rewrite without performing it.

    ``purge_paths``        — remove the file from all history entirely.
    ``strip_output_paths`` — keep the file but blank notebook outputs in every
                             historical revision (via a blob callback).
    """
    cmds = []
    if purge_paths:
        args = " ".join(f"--path {p!r} " for p in purge_paths)
        cmds.append(f"git filter-repo --invert-paths {args}".strip())
    if strip_output_paths:
        cmds.append("git filter-repo --blob-callback <notebook output stripper>")
    if redact_identifiers:
        cmds.append("git filter-repo --blob-callback <identifier redactor>")
    return RewritePlan(repo=repo, paths_to_purge=purge_paths,
                       paths_to_strip_outputs=strip_output_paths,
                       redact_identifiers=redact_identifiers,
                       normalise_cell_ids=normalise_cell_ids, profile=profile,
                       tool=find_filter_repo() or "MISSING", commands=cmds)


# Applied to every blob. It self-selects notebooks by sniffing the JSON, which
# is why no --path filter is used: in git-filter-repo, `--path X` means "keep
# only X" and would delete every other file from history.
BLOB_CALLBACK = r'''
import json
data = blob.data
if data[:1] == b"{" and b'"cells"' in data[:4096]:
    try:
        nb = json.loads(data.decode("utf-8"))
    except Exception:
        nb = None
    if nb is not None and "cells" in nb:
        changed = False
        for cell in nb["cells"]:
            if cell.get("cell_type") == "code" and cell.get("outputs"):
                cell["outputs"] = []
                cell["execution_count"] = None
                changed = True
        if changed:
            blob.data = (json.dumps(nb, indent=1) + "\n").encode("utf-8")
'''


def mirror_clone(remote: str, dest: Path) -> Path:
    """Fresh --mirror clone: the only way to rewrite *every* branch and tag.

    An in-place rewrite of a working clone touches only the refs that exist
    locally, silently leaving branches that live only on the remote untouched.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    subprocess.run(["git", "clone", "--mirror", remote, str(dest)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return dest


def run_rewrite(plan: RewritePlan, execute: bool = False,
                backup_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """Perform the rewrite in place. Returns (ran, message).

    Refuses unless ``execute`` is True and a backup directory is given. Never
    pushes — publishing the rewrite stays a deliberate manual step.
    """
    if plan.tool == "MISSING":
        return False, ("git-filter-repo not installed. Install with "
                       "`conda install -c conda-forge git-filter-repo` or "
                       "`pip install git-filter-repo` (a single file, no deps).")
    if not execute:
        return False, "dry run — pass --execute to perform the rewrite"
    if backup_dir is None:
        return False, "refusing to rewrite without a backup directory"

    plan.backup = backup_repo(plan.repo, backup_dir)

    if plan.paths_to_strip_outputs:
        # No --path here: it is a keep-only filter, and combining it with a
        # blob callback would delete every unlisted file from history. The
        # callback selects notebooks itself.
        proc = subprocess.run(
            ["git", "filter-repo", "--force", "--blob-callback", BLOB_CALLBACK],
            cwd=str(plan.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            return False, proc.stdout.decode("utf-8", "replace")

    if plan.normalise_cell_ids:
        if plan.profile is None:
            return False, "cell-id normalisation requires a profile"
        proc = subprocess.run(
            ["git", "filter-repo", "--force", "--blob-callback",
             build_cell_id_callback(plan.profile)],
            cwd=str(plan.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            return False, proc.stdout.decode("utf-8", "replace")

    if plan.redact_identifiers:
        if plan.profile is None:
            return False, "redaction requires a profile"
        proc = subprocess.run(
            ["git", "filter-repo", "--force", "--blob-callback",
             build_callback(plan.profile)],
            cwd=str(plan.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            return False, proc.stdout.decode("utf-8", "replace")

    if plan.paths_to_purge:
        args = ["git", "filter-repo", "--force", "--invert-paths"]
        for p in plan.paths_to_purge:
            args += ["--path", p]
        proc = subprocess.run(args, cwd=str(plan.repo),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            return False, proc.stdout.decode("utf-8", "replace")

    return True, (
        f"rewrite complete; backup at {plan.backup}\n"
        "filter-repo removes the 'origin' remote as a safety measure — re-add it "
        "before pushing.\n"
        "History has NOT been pushed. Verify with `git-audit`, then force-push "
        "manually."
    )
