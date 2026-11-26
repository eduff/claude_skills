"""Filesystem inventory and rule-based classification.

Walks one or more roots and assigns every file a *category* (what it is) and a
*disposition* (what should happen to it) by evaluating an ordered list of rules
from the active profile. First matching rule wins; anything unmatched falls
through to ``REVIEW`` so nothing is silently assumed safe.

Dispositions
------------
``KEEP``    — code, aggregate results, documentation. Stays.
``REVIEW``  — cannot be classified confidently; a human decides.
``PURGE``   — individual-level or raw source data slated for removal.

Classification is deliberately rule-driven rather than hardcoded so that the
decisions are auditable, reviewable in a diff, and tunable per project without
touching the toolkit.
"""
from __future__ import annotations

import fnmatch
import os
import stat as stat_mod
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .profile import Profile

DISPOSITIONS = ("KEEP", "REVIEW", "PURGE")


@dataclass
class Rule:
    name: str
    category: str
    disposition: str
    reason: str = ""
    path_glob: Sequence[str] = ()
    ext: Sequence[str] = ()
    name_regex: Optional["re.Pattern"] = None
    min_mb: float = 0.0
    max_mb: float = float("inf")
    # A KEEP decided by name / location alone is not enough for data files: a
    # per-participant pickle in a results folder matches the same path glob as a
    # table of hazard ratios. With content_check the file is opened and must be
    # verifiably aggregate (scan.content_verdict) or it drops to REVIEW.
    content_check: bool = False

    def matches(self, path: Path, size_mb: float) -> bool:
        if self.ext and path.suffix.lower() not in self.ext:
            return False
        if not (self.min_mb <= size_mb <= self.max_mb):
            return False
        if self.name_regex and not self.name_regex.search(path.name):
            return False
        if self.path_glob:
            s = str(path)
            if not any(fnmatch.fnmatch(s, g) for g in self.path_glob):
                return False
        return bool(self.ext or self.path_glob or self.name_regex
                    or self.min_mb > 0 or self.max_mb < float("inf"))


def compile_rules(profile: Profile) -> List[Rule]:
    rules = []
    for spec in profile.raw.get("classification", []):
        disp = spec.get("disposition", "REVIEW").upper()
        if disp not in DISPOSITIONS:
            raise ValueError(f"rule {spec.get('name')!r}: bad disposition {disp!r}")
        rules.append(Rule(
            name=spec["name"],
            category=spec.get("category", "other"),
            disposition=disp,
            reason=spec.get("reason", ""),
            path_glob=tuple(spec.get("path_glob", [])),
            ext=tuple(e.lower() for e in spec.get("ext", [])),
            name_regex=re.compile(spec["name_regex"]) if spec.get("name_regex") else None,
            min_mb=float(spec.get("min_mb", 0)),
            max_mb=float(spec.get("max_mb", float("inf"))),
            content_check=bool(spec.get("content_check", False)),
        ))
    return rules


@dataclass
class Entry:
    path: Path
    size: int
    mtime: float
    category: str = "unclassified"
    disposition: str = "REVIEW"
    rule: str = ""
    reason: str = ""
    is_symlink: bool = False

    @property
    def size_mb(self) -> float:
        return self.size / 1024 / 1024

    def as_dict(self) -> dict:
        return {
            "path": str(self.path), "size": self.size, "mtime": self.mtime,
            "category": self.category, "disposition": self.disposition,
            "rule": self.rule, "reason": self.reason, "is_symlink": self.is_symlink,
        }


@dataclass
class Inventory:
    roots: List[Path]
    entries: List[Entry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # (st_dev, st_ino) of every file already counted. Two roots can reach the
    # same file when one is a symlink to the other (/mnt/data -> /mnt/disk1), and
    # hardlinks do the same within a root. Counting either twice inflates every
    # total and would double-count a purge.
    _seen: set = field(default_factory=set, repr=False)
    duplicates: List[str] = field(default_factory=list)
    vendor_dirs_skipped: List[str] = field(default_factory=list)
    vendor_bytes_skipped: int = 0

    def by_disposition(self, disp: str) -> List[Entry]:
        return [e for e in self.entries if e.disposition == disp]

    def totals(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for e in self.entries:
            d = out.setdefault(e.disposition, {"files": 0, "bytes": 0})
            d["files"] += 1
            d["bytes"] += e.size
        return out

    def by_category(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for e in self.entries:
            d = out.setdefault(e.category, {"files": 0, "bytes": 0, "disposition": e.disposition})
            d["files"] += 1
            d["bytes"] += e.size
        return out


def build(roots: Iterable[Path], profile: Profile,
          follow_symlinks: bool = False,
          min_report_mb: float = 0.0,
          progress: bool = False,
          skip_vendor: bool = True) -> Inventory:
    """Walk *roots* and classify every file.

    ``skip_vendor`` prunes installed dependencies (conda/R/node trees). Their
    total size is still measured and reported, so the accounting stays honest
    even though the files are not individually classified.
    """
    rules = compile_rules(profile)
    inv = Inventory(roots=[Path(r) for r in roots])
    seen_dirs = 0
    for root in inv.roots:
        if not root.exists():
            inv.errors.append(f"{root}: not found")
            continue
        if root.is_file():
            _add(inv, root, rules, profile, min_report_mb)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks,
                                                    onerror=lambda e: inv.errors.append(str(e))):
            keep = []
            for name in sorted(dirnames):
                if name in profile.skip_dirs:
                    continue
                if skip_vendor and any(fnmatch.fnmatch(name, pat)
                                       for pat in profile.vendor_dirs):
                    full = Path(dirpath) / name
                    inv.vendor_dirs_skipped.append(str(full))
                    inv.vendor_bytes_skipped += _dir_size(full)
                    continue
                keep.append(name)
            dirnames[:] = keep
            seen_dirs += 1
            if progress and seen_dirs % 500 == 0:
                print(f"    ... {seen_dirs} dirs, {len(inv.entries)} files", flush=True)
            for fn in filenames:
                _add(inv, Path(dirpath) / fn, rules, profile, min_report_mb)
    inv.entries.sort(key=lambda e: -e.size)
    return inv


def _add(inv: Inventory, path: Path, rules: List[Rule], profile: Profile,
         min_report_mb: float) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        inv.errors.append(f"{path}: {exc}")
        return
    is_link = os.path.islink(str(path))
    size = 0 if is_link else st.st_size
    size_mb = size / 1024 / 1024
    if size_mb < min_report_mb:
        return
    if not is_link:
        key = (st.st_dev, st.st_ino)
        if key in inv._seen:
            inv.duplicates.append(str(path))
            return
        inv._seen.add(key)
    entry = Entry(path=path, size=size, mtime=st.st_mtime, is_symlink=is_link)
    if is_link:
        entry.category = "symlink"
        entry.disposition = "REVIEW"
        entry.rule = "symlink"
        entry.reason = "symlink — resolve target before acting"
        inv.entries.append(entry)
        return
    for rule in rules:
        if rule.matches(path, size_mb):
            entry.category = rule.category
            entry.disposition = rule.disposition
            entry.rule = rule.name
            entry.reason = rule.reason
            if rule.disposition == "KEEP" and rule.content_check:
                _verify_keep(entry, path, profile)
            break
    else:
        # Unmatched: bulk data extensions default to REVIEW-as-data, tiny text
        # files to REVIEW-as-other. Nothing defaults to KEEP or PURGE.
        if path.suffix.lower() in profile.bulk_data_ext:
            entry.category = "unclassified_data"
            entry.reason = "data-shaped file matched no rule"
        else:
            entry.category = "unclassified"
            entry.reason = "matched no rule"
    # A HIGH-severity sensitive filename (e.g. a withdrawal list) is never kept
    # on the strength of its extension alone: a .txt of participant IDs would
    # otherwise be KEEP as "documentation".
    # Nor is a filename carrying an identifier: per-participant outputs such as
    # <id>_<field>_<instance>.txt look like documentation by extension.
    if entry.disposition == "KEEP":
        for m in profile.id_regex.finditer(path.name):
            v = m.group()
            if profile.in_range(int(v)) and not profile.allowed(v):
                entry.disposition = "REVIEW"
                entry.category = "id_in_filename"
                entry.rule += "+id_name"
                entry.reason = f"{entry.reason}; filename contains a {profile.id_label}-range value"
                break
    if entry.disposition == "KEEP":
        for nr in profile.sensitive_filenames:
            if nr.severity == "HIGH" and nr.regex.search(path.name):
                entry.disposition = "REVIEW"
                entry.category = "sensitive_filename"
                entry.rule += "+sensitive_name"
                entry.reason = f"{entry.reason}; sensitive filename: {nr.note}"
                break
    inv.entries.append(entry)


def _verify_keep(entry: "Entry", path: Path, profile: Profile) -> None:
    """Downgrade a content-checked KEEP to REVIEW unless the file is verifiably
    aggregate. Only ever moves KEEP -> REVIEW (the conservative direction)."""
    from .scan import content_verdict
    verdict, why = content_verdict(path, profile)
    if verdict == "aggregate":
        entry.reason = f"{entry.reason}; content check: {why}"
        return
    entry.disposition = "REVIEW"
    entry.category = "individual_derived" if verdict == "individual" else "unverified_data"
    entry.rule = f"{entry.rule}+content"
    entry.reason = (f"{entry.reason} — but content check says {verdict.upper()}: {why}")


def _dir_size(path: Path) -> int:
    """Total bytes under *path*, so a pruned dependency tree is still counted."""
    total = 0
    for dirpath, _, filenames in os.walk(path, onerror=lambda e: None):
        for fn in filenames:
            try:
                st = (Path(dirpath) / fn).lstat()
            except OSError:
                continue
            if not stat_mod.S_ISLNK(st.st_mode):
                total += st.st_size
    return total


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024 or unit == "TB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{int(nbytes)} B"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"
