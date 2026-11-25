"""Git exposure auditing.

Answers the question a filesystem scan cannot: *is this file exposed?*

A file may be clean in the working tree yet still carry identifiers in every
historical commit, and a repo with a remote has already published whatever its
history contains. This module scans:

  * the working tree vs ``git ls-files``  (tracked / untracked / ignored)
  * every blob reachable from every ref   (historical exposure)
  * remotes and push state                (has it left the machine?)
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .profile import Profile
from .scan import (FileReport, classify, notebook_output_text,
                   notebook_source_text, scan_text)

# Extensions never worth decoding in history: binary formats that cannot hold
# readable identifiers. Everything else IS scanned — an allowlist would miss a
# .dat, an extensionless file, or an unfamiliar format, which is exactly where
# an unnoticed leak hides. Undecodable blobs are skipped at read time.
HISTORY_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".tiff",
                    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z",
                    ".so", ".dylib", ".dll", ".pyc", ".pyo", ".o", ".a",
                    ".woff", ".woff2", ".ttf", ".otf", ".eot",
                    ".mp4", ".mp3", ".wav", ".mov", ".avi"}


class NotAGitRepo(RuntimeError):
    pass


def _git(repo: Path, *args: str, binary: bool = False, check: bool = True):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:3])}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def is_git_repo(path: Path) -> bool:
    try:
        _git(path, "rev-parse", "--git-dir")
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def is_bare(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-bare-repository", check=False).strip() == "true"


def repo_root(path: Path) -> Path:
    """Working-tree root, or the git dir itself for a bare repo.

    Mirror clones are bare, and a mirror clone is precisely what you audit
    while rewriting history — so this must not assume a work tree exists.
    """
    if is_bare(path):
        return Path(_git(path, "rev-parse", "--absolute-git-dir").strip())
    return Path(_git(path, "rev-parse", "--show-toplevel").strip())


@dataclass
class RemoteState:
    remotes: Dict[str, str] = field(default_factory=dict)
    branch: str = ""
    upstream: Optional[str] = None
    ahead: int = 0
    behind: int = 0

    @property
    def has_remote(self) -> bool:
        return bool(self.remotes)

    @property
    def fully_pushed(self) -> bool:
        return self.upstream is not None and self.ahead == 0


def remote_state(repo: Path) -> RemoteState:
    st = RemoteState()
    for line in _git(repo, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "(push)":
            st.remotes[parts[0]] = parts[1]
    st.branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
              check=False).strip()
    if up and "fatal" not in up:
        st.upstream = up
        counts = _git(repo, "rev-list", "--left-right", "--count", f"{up}...HEAD",
                      check=False).split()
        if len(counts) == 2:
            st.behind, st.ahead = int(counts[0]), int(counts[1])
    return st


def tracked_files(repo: Path) -> Set[str]:
    out = _git(repo, "ls-files", "-z")
    return {p for p in out.split("\0") if p}


def ignored_files(repo: Path) -> Set[str]:
    out = _git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
               check=False)
    return {p for p in out.split("\0") if p}


# ---------------------------------------------------------------------------
# History blob scanning
# ---------------------------------------------------------------------------

@dataclass
class HistoryHit:
    path: str
    sha: str
    hits: int
    confidence: str
    first_commit: str = ""
    n_versions: int = 1
    examples: List[str] = field(default_factory=list)


def _all_blobs(repo: Path, paths: Optional[Iterable[str]] = None
               ) -> List[Tuple[str, str, int]]:
    """Return (sha, path, size) for every blob reachable from any ref."""
    objects = _git(repo, "rev-list", "--objects", "--all")
    wanted = set(paths) if paths else None
    entries: Dict[str, str] = {}
    for line in objects.splitlines():
        sha, _, name = line.partition(" ")
        if not name:
            continue
        if wanted is not None and name not in wanted:
            continue
        entries[sha] = name
    if not entries:
        return []
    # Batch-check types and sizes in one call rather than one process per object.
    inp = "\n".join(entries).encode()
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out: List[Tuple[str, str, int]] = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            sha, _, size = parts
            out.append((sha, entries[sha], int(size)))
    return out


def _scan_blob(repo: Path, sha: str, path: str, profile: Profile, sample: int
               ) -> Tuple[int, int, List[str]]:
    raw = _git(repo, "cat-file", "blob", sha, binary=True)
    text = raw.decode("utf-8", "replace")
    if path.lower().endswith(".ipynb"):
        import json
        try:
            nb = json.loads(text)
        except json.JSONDecodeError:
            r = scan_text(text, profile, max_examples=sample)
            return r.total, r.supported, r.examples
        total = supported = 0
        examples: List[str] = []
        for cell in nb.get("cells", []):
            kind = cell.get("cell_type")
            if kind not in ("code", "markdown"):
                continue
            # Outputs AND source. A hardcoded identifier in a code cell is an
            # exposure that clearing outputs does not remove.
            blobs = []
            if kind == "code":
                blobs.append(("", "".join(notebook_output_text(cell))))
            blobs.append(("[SOURCE] ", notebook_source_text(cell)))
            for label, text_blob in blobs:
                if not text_blob:
                    continue
                res = scan_text(text_blob, profile, max_examples=sample)
                total += res.total
                supported += res.supported
                for e in res.examples:
                    if len(examples) < sample:
                        examples.append(label + e)
        return total, supported, examples
    r = scan_text(text, profile, max_examples=sample)
    return r.total, r.supported, r.examples


def scan_history(repo: Path, profile: Profile, sample: int = 3,
                 max_blob_mb: float = 100.0,
                 progress: bool = False) -> List[HistoryHit]:
    """Scan every reachable blob for identifiers. Returns one entry per path."""
    blobs = _all_blobs(repo)
    by_path: Dict[str, HistoryHit] = {}
    cap = max_blob_mb * 1024 * 1024
    scanned = 0
    for sha, path, size in blobs:
        ext = Path(path).suffix.lower()
        if ext in HISTORY_SKIP_EXT or size > cap:
            continue
        scanned += 1
        if progress and scanned % 200 == 0:
            print(f"    ... {scanned} blobs scanned", flush=True)
        total, supported, examples = _scan_blob(repo, sha, path, profile, sample)
        conf = classify(total, supported, "notebook" if ext == ".ipynb" else "text")
        if conf in ("NONE", "LOW"):
            continue
        prev = by_path.get(path)
        if prev is None:
            by_path[path] = HistoryHit(path=path, sha=sha, hits=total,
                                       confidence=conf, examples=examples)
        else:
            prev.n_versions += 1
            if total > prev.hits:
                prev.hits, prev.sha, prev.confidence, prev.examples = (
                    total, sha, conf, examples)
    for hit in by_path.values():
        hit.first_commit = _first_commit_touching(repo, hit.path)
    return sorted(by_path.values(),
                  key=lambda h: (-h.hits, h.path))


def _first_commit_touching(repo: Path, path: str) -> str:
    out = _git(repo, "log", "--all", "--reverse", "--format=%h %ad", "--date=short",
               "-1", "--", path, check=False).strip()
    return out.splitlines()[0] if out else ""


# ---------------------------------------------------------------------------
# Combined audit
# ---------------------------------------------------------------------------

@dataclass
class GitAudit:
    repo: Path
    remote: RemoteState
    bare: bool = False
    n_tracked: int = 0
    tracked_flagged: List[FileReport] = field(default_factory=list)
    history_hits: List[HistoryHit] = field(default_factory=list)
    tracked_bulk: List[Tuple[str, int]] = field(default_factory=list)
    history_only: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.tracked_flagged or self.history_hits or self.tracked_bulk)


def audit_repo(repo: Path, profile: Profile, include_history: bool = True,
               sample: int = 3, progress: bool = False) -> GitAudit:
    if not is_git_repo(repo):
        raise NotAGitRepo(f"{repo} is not a git repository")
    root = repo_root(repo)
    bare = is_bare(repo)
    # A bare repo has no work tree: only history and remotes can be audited.
    tracked = set() if bare else tracked_files(root)

    from .scan import dispatch
    flagged: List[FileReport] = []
    bulk: List[Tuple[str, int]] = []
    for rel in sorted(tracked):
        fp = root / rel
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        size = fp.stat().st_size
        if ext in profile.bulk_data_ext and size > profile.bulk_data_mb * 1024 * 1024:
            bulk.append((rel, size))
        r = dispatch(fp, profile, sample)
        if r.flagged:
            r.path = Path(rel)
            flagged.append(r)

    hist = scan_history(root, profile, sample, progress=progress) if include_history else []
    hist_only = sorted({h.path for h in hist} - {str(f.path) for f in flagged})

    return GitAudit(
        repo=root,
        remote=remote_state(root),
        bare=bare,
        n_tracked=len(tracked),
        tracked_flagged=sorted(flagged, key=lambda r: -r.hits),
        history_hits=hist,
        tracked_bulk=sorted(bulk, key=lambda t: -t[1]),
        history_only=hist_only,
    )
