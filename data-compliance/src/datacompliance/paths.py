"""Scan for server-identifying paths, hostnames and credentials in source files.

Distinct from identifier scanning: nothing here is participant data, but all of
it either identifies the machine/user (blocking a public or cross-site release)
or is a secret that should never have been committed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

from .profile import Profile
from .scan import _size, walk

# Only source-like files: a hardcoded path inside a data blob is not actionable.
SOURCE_EXT = {".py", ".r", ".rmd", ".qmd", ".sh", ".bash", ".zsh", ".pl", ".jl",
              ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".cfg",
              ".ini", ".ipynb", ".html", ".tex", ".mk"}

# Source text is small even when the containing file is not, so cap generously
# but well below the identifier scanner's limit.
MAX_SOURCE_MB = 8.0


@dataclass
class PathHit:
    pattern: str
    severity: str
    hint: str
    matches: Dict[str, int] = field(default_factory=dict)   # matched text -> count
    lines: List[str] = field(default_factory=list)          # "line_no: excerpt"


@dataclass
class PathReport:
    path: Path
    size_bytes: int = 0
    hits: List[PathHit] = field(default_factory=list)
    error: str = ""

    @property
    def worst(self) -> str:
        order = ["HIGH", "MEDIUM", "LOW"]
        for sev in order:
            if any(h.severity == sev for h in self.hits):
                return sev
        return "NONE"

    @property
    def total(self) -> int:
        return sum(sum(h.matches.values()) for h in self.hits)


def _allowed(text: str, profile: Profile) -> bool:
    return any(a in text for a in profile.path_pattern_allow)


def _source_text(path: Path) -> str:
    """Read a file's *source*. For notebooks that means cell source only —
    outputs are the identifier scanner's job and dominate the file size."""
    if path.suffix.lower() == ".ipynb":
        import json
        with open(path, "r", errors="replace") as f:
            nb = json.load(f)
        parts = []
        for cell in nb.get("cells", []):
            src = cell.get("source", [])
            parts.append("".join(src) if isinstance(src, list) else str(src))
        return "\n".join(parts)
    return path.read_text(errors="replace")


# Lines longer than this are data blobs (base64, minified JSON, wide CSV rows),
# not source. Skipping them also bounds regex backtracking, which is otherwise
# quadratic on long runs of path/email-like characters.
MAX_LINE_CHARS = 2000


def scan_file(path: Path, profile: Profile, max_examples: int = 3) -> PathReport:
    rep = PathReport(path=path, size_bytes=_size(path))
    if path.suffix.lower() not in SOURCE_EXT:
        return rep
    if rep.size_bytes > MAX_SOURCE_MB * 1024 * 1024 and path.suffix.lower() != ".ipynb":
        rep.error = f"skipped: {rep.size_bytes / 1024 / 1024:.0f} MB"
        return rep
    try:
        text = _source_text(path)
    except (OSError, ValueError) as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
        return rep
    if len(text) > MAX_SOURCE_MB * 1024 * 1024:
        rep.error = f"skipped: {len(text) / 1024 / 1024:.0f} MB of source"
        return rep

    hits = {}
    n_long = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > MAX_LINE_CHARS:
            n_long += 1
            continue
        for pat in profile.path_patterns:
            for m in pat.regex.finditer(line):
                matched = m.group(0)
                if _allowed(matched, profile):
                    continue
                hit = hits.get(pat.name)
                if hit is None:
                    hit = hits[pat.name] = PathHit(
                        pattern=pat.name, severity=pat.severity, hint=pat.hint)
                redact = pat.name in (
                    "generic_api_token", "aws_access_key", "private_key_block")
                key = f"<{pat.name} redacted>" if redact else matched
                hit.matches[key] = hit.matches.get(key, 0) + 1
                if len(hit.lines) < max_examples:
                    excerpt = "<redacted>" if redact else line.strip()[:120]
                    hit.lines.append(f"{lineno}: {excerpt}")
    if n_long:
        rep.error = f"{n_long} over-long line(s) skipped"
    rep.hits = [hits[p.name] for p in profile.path_patterns if p.name in hits]
    return rep


def scan_paths(roots: Iterable[Path], profile: Profile, recursive: bool = True,
               max_examples: int = 3, on_file=None) -> List[PathReport]:
    out = []
    for f in walk(list(roots), profile, recursive=recursive, skip_vendor=True):
        rep = scan_file(f, profile, max_examples)
        if on_file is not None:
            on_file(f, rep.total)
        if rep.hits or rep.error:
            out.append(rep)
    return out


def summarise(reports: List[PathReport]) -> Dict[str, Dict[str, object]]:
    """Aggregate by pattern: which distinct strings appear, in how many files."""
    agg: Dict[str, Dict[str, object]] = {}
    for rep in reports:
        for hit in rep.hits:
            entry = agg.setdefault(hit.pattern, {
                "severity": hit.severity, "hint": hit.hint,
                "files": set(), "strings": {}, "total": 0,
            })
            entry["files"].add(str(rep.path))
            entry["total"] += sum(hit.matches.values())
            for text, n in hit.matches.items():
                entry["strings"][text] = entry["strings"].get(text, 0) + n
    return agg


def check_filenames(roots: Iterable[Path], profile: Profile,
                    recursive: bool = True) -> List[tuple]:
    """Flag files whose *name* marks them sensitive (withdrawal lists, keys)."""
    out = []
    for f in walk(list(roots), profile, recursive=recursive, skip_vendor=True):
        for rule in profile.sensitive_filenames:
            if rule.regex.search(f.name):
                out.append((f, rule.severity, rule.note))
                break
    return out
