"""Regenerability analysis: can a data file be rebuilt if it is removed?

Deleting individual-level data is only safe if the file can be regenerated from
code plus a source that still exists. This module searches the codebase for the
statements that *write* each target and the statements that *read* it, then
issues a verdict.

The verdicts are deliberately cautious. ``NO_GENERATOR`` does not prove a file
is irreproducible — the generator may be a notebook cell, an ad-hoc session, or
a script under a different name — which is precisely why it must be reviewed by
a human before anything is removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .profile import Profile
from .scan import walk

CODE_EXT = {".py", ".r", ".rmd", ".qmd", ".sh", ".bash", ".jl", ".ipynb", ".md"}

WRITE_VERBS = (
    "to_pickle", "to_parquet", "to_feather", "to_csv", "to_hdf", "to_stata",
    "pickle.dump", "joblib.dump", "np.save", "np.savez", "torch.save",
    "saveRDS", "save(", "write_parquet", "write_csv", "write.csv",
    "fwrite", "h5py.File", "HDFStore",
)
READ_VERBS = (
    "read_pickle", "read_parquet", "read_feather", "read_csv", "read_hdf",
    "read_stata", "pickle.load", "joblib.load", "np.load", "torch.load",
    "readRDS", "load(", "read_parquet", "read.csv", "fread",
)
_WB = re.compile(r"open\s*\([^)]*['\"][wa]b?['\"]")
_RB = re.compile(r"open\s*\([^)]*['\"]rb?['\"]")
# A commented-out write is not a generator. This is the common failure mode:
# the cell that built a file survives only as a comment.
_COMMENT = re.compile(r"^\s*(#|//|%|--)")

VERDICTS = {
    "REGENERABLE": "A write statement was found in tracked code",
    "GENERATOR_UNTRACKED": "Only untracked or notebook code writes this file",
    "NO_GENERATOR": "No code found that writes this file",
    "GENERATOR_COMMENTED_OUT": "The only write statement is commented out",
    "CHAIN_AT_RISK": "Generator reads a source that is itself marked for removal",
    "ORPHAN": "Nothing reads or writes it",
}


@dataclass
class Ref:
    file: Path
    line: int
    text: str
    kind: str          # "write" | "read" | "mention"
    tracked: bool = False
    cell: Optional[int] = None

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}"
        if self.cell is not None:
            loc += f" (cell {self.cell})"
        return f"{loc}  [{self.kind}] {self.text[:100]}"


@dataclass
class Provenance:
    target: Path
    writers: List[Ref] = field(default_factory=list)
    readers: List[Ref] = field(default_factory=list)
    mentions: List[Ref] = field(default_factory=list)
    commented_writers: List[Ref] = field(default_factory=list)
    verdict: str = "NO_GENERATOR"
    detail: str = ""
    upstream: List[str] = field(default_factory=list)

    @property
    def safe_to_purge(self) -> bool:
        return self.verdict == "REGENERABLE"

    def as_dict(self) -> dict:
        return {
            "target": str(self.target), "verdict": self.verdict, "detail": self.detail,
            "writers": [str(r) for r in self.writers],
            "readers": [str(r) for r in self.readers][:20],
            "n_mentions": len(self.mentions),
            "upstream_at_risk": self.upstream,
        }


def _iter_code_lines(path: Path):
    """Yield (lineno, text, cell_index) for a source file; notebooks by cell."""
    if path.suffix.lower() == ".ipynb":
        import json
        try:
            nb = json.loads(path.read_text(errors="replace"))
        except Exception:
            return
        for ci, cell in enumerate(nb.get("cells", [])):
            if cell.get("cell_type") != "code":
                continue
            src = cell.get("source", [])
            text = "".join(src) if isinstance(src, list) else str(src)
            for i, line in enumerate(text.splitlines(), 1):
                yield i, line, ci
        return
    try:
        for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if len(line) < 4000:
                yield i, line, None
    except OSError:
        return


def _classify(line: str, commented: bool = False) -> str:
    if any(v in line for v in WRITE_VERBS) or _WB.search(line):
        return "commented_write" if commented else "write"
    if any(v in line for v in READ_VERBS) or _RB.search(line):
        return "read"
    return "mention"


def _is_comment(line: str) -> bool:
    return bool(_COMMENT.match(line))


def index_codebase(roots: Iterable[Path], profile: Profile,
                   targets: Sequence[str],
                   tracked: Optional[Set[str]] = None,
                   context: int = 2) -> Dict[str, List[Ref]]:
    """One pass over the codebase, collecting references to every target name.

    ``targets`` are basenames (``df_core.pkl``). Matching also accepts the stem
    (``df_core``) so that paths assembled from variables are still found.
    """
    needles = {}
    for t in targets:
        stem = Path(t).stem
        needles[t] = (t, stem)

    found: Dict[str, List[Ref]] = {t: [] for t in targets}
    for f in walk(list(roots), profile, recursive=True, skip_vendor=True):
        if f.suffix.lower() not in CODE_EXT:
            continue
        is_tracked = tracked is None or str(f) in tracked or _rel_in(f, tracked)
        lines = list(_iter_code_lines(f))
        if not lines:
            continue
        joined = [l for _, l, _ in lines]
        for target, (full, stem) in needles.items():
            for idx, (lineno, line, cell) in enumerate(lines):
                exact = full in line
                if not exact and not _stem_match(line, stem):
                    continue
                commented = _is_comment(line)
                kind = _classify(line, commented)
                if kind == "mention":
                    # A write verb may sit on a neighbouring line:
                    #     df.to_pickle(
                    #         path)
                    lo = max(0, idx - context)
                    hi = min(len(joined), idx + context + 1)
                    window = " ".join(joined[lo:hi])
                    kind = _classify(window, commented and all(
                        _is_comment(l) for l in joined[lo:hi] if l.strip()))
                # Only an exact basename match may establish a generator; a bare
                # stem also matches sibling files (other_columns_for_review.txt)
                # and same-named DataFrame variables.
                if kind == "write" and not exact:
                    kind = "mention"
                found[target].append(Ref(file=f, line=lineno, text=line.strip(),
                                         kind=kind, tracked=is_tracked, cell=cell))
    return found


def _stem_match(line: str, stem: str) -> bool:
    """Match a bare stem only at a word boundary, so `df_core` does not match
    `df_core_icd_expanded` and `other_columns` does not match
    `other_columns_for_review`."""
    i = line.find(stem)
    while i != -1:
        before = line[i - 1] if i else " "
        after = line[i + len(stem)] if i + len(stem) < len(line) else " "
        if not (before.isalnum() or before == "_") and after in ".'\"`) ,]}":
            return True
        i = line.find(stem, i + 1)
    return False


def _rel_in(path: Path, tracked: Set[str]) -> bool:
    parts = path.parts
    for i in range(len(parts)):
        if "/".join(parts[i:]) in tracked:
            return True
    return False


def analyse(targets: Sequence[Path], roots: Iterable[Path], profile: Profile,
            tracked: Optional[Set[str]] = None,
            purge_names: Optional[Set[str]] = None) -> List[Provenance]:
    """Produce a regenerability verdict for each target file."""
    names = sorted({t.name for t in targets})
    refs = index_codebase(roots, profile, names, tracked=tracked)
    purge_names = purge_names or set()

    out: List[Provenance] = []
    seen: Set[str] = set()
    for t in targets:
        if t.name in seen:
            continue
        seen.add(t.name)
        p = Provenance(target=t)
        for r in refs.get(t.name, []):
            (p.writers if r.kind == "write" else
             p.commented_writers if r.kind == "commented_write" else
             p.readers if r.kind == "read" else p.mentions).append(r)

        tracked_writers = [w for w in p.writers
                           if w.tracked and w.file.suffix.lower() != ".ipynb"]
        if tracked_writers:
            p.verdict = "REGENERABLE"
            p.detail = f"written by {len(tracked_writers)} tracked script(s)"
        elif p.writers:
            nb = sum(1 for w in p.writers if w.file.suffix.lower() == ".ipynb")
            p.verdict = "GENERATOR_UNTRACKED"
            p.detail = (f"only {'notebook' if nb else 'untracked'} code writes this "
                        f"({len(p.writers)} site(s)) — derivation is not captured "
                        f"in a runnable, version-controlled script")
        elif p.commented_writers:
            p.verdict = "GENERATOR_COMMENTED_OUT"
            w = p.commented_writers[0]
            p.detail = (f"the only write statement is commented out "
                        f"({w.file.name}"
                        + (f" cell {w.cell}" if w.cell is not None else f":{w.line}")
                        + f") — {len(p.readers)} reader(s)")
        elif p.readers or p.mentions:
            p.verdict = "NO_GENERATOR"
            p.detail = (f"read in {len(p.readers)} place(s) but no write statement "
                        f"found — cannot be rebuilt from this codebase")
        else:
            p.verdict = "ORPHAN"
            p.detail = "no code refers to this file at all"

        # Does the generator depend on something also being removed?
        if p.verdict in ("REGENERABLE", "GENERATOR_UNTRACKED"):
            gen_files = {w.file for w in p.writers}
            upstream = set()
            for gf in gen_files:
                for _, line, _ in _iter_code_lines(gf):
                    for name in purge_names:
                        if name != t.name and name in line and _classify(line) == "read":
                            upstream.add(name)
            if upstream:
                p.upstream = sorted(upstream)
                p.verdict = "CHAIN_AT_RISK"
                p.detail = (f"generator reads {len(upstream)} file(s) that are "
                            f"themselves marked for removal")
        out.append(p)
    rank = {"ORPHAN": 0, "NO_GENERATOR": 1, "GENERATOR_COMMENTED_OUT": 2,
            "GENERATOR_UNTRACKED": 3, "CHAIN_AT_RISK": 4, "REGENERABLE": 5}
    return sorted(out, key=lambda p: (rank.get(p.verdict, 9), str(p.target)))
