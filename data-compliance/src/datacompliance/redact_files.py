"""Mask participant identifiers inside files kept for archival interest.

Where deleting a file is more than the situation needs — session transcripts,
notebooks, shell / IPython history — identifiers can be masked in place
instead. Unlike the scanners, which lower confidence on statistical context,
masking acts on **every** in-range match of the profile's identifier regex
(only URL contexts and declared allow-values are spared): a transcript cannot
be trusted to say which seven-digit number was a participant.

Handled per file type, keeping every file valid:

* ``.jsonl`` (e.g. Claude Code transcripts) — each line is parsed and only
  string values are masked; structural fields (``uuid``, ``parentUuid``,
  ``*Id``, ``timestamp``, ``backupFileName`` …) are left alone so threading and
  resumption still work. Lines without a match are written back byte-identical.
* ``.ipynb`` — code-cell source is masked; outputs of any cell whose text
  output holds a match are cleared (images are not text and are left).
* IPython ``history.sqlite`` — matching ``history`` / ``output_history`` rows
  are masked, then ``VACUUM`` so old pages do not keep the values.
* plain text (``.txt``, ``.log``, ``.md``, shell histories …) — masked in place.

Safety: dry run by default (counts only, values never printed); files modified
within ``min_age_min`` minutes are refused, since a live transcript or history
may be appended to concurrently; writes are atomic (temp file + rename,
permissions kept). No backup copy is made — a backup would keep the values.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .profile import Profile

PLACEHOLDER = "REDACTED_ID"
URL = re.compile(r"https?://\S*|www\.\S*|doi\.org\S*|stackoverflow\.com\S*|github\.com\S*|gitlab\.\S*")
# JSON keys whose values are structure, not content.
SKIP_KEYS = {"uuid", "parentuuid", "leafuuid", "messageid", "sessionid", "requestid", "id",
             "tool_use_id", "timestamp", "version", "backupfilename", "promptid", "agentid",
             "sourcetoolassistantuuid", "cwd", "gitbranch", "slug"}
TEXT_EXT = {".txt", ".log", ".md", ".csv", ".tsv", ".py", ".r", ".sh", ".json"}
TEXT_NAMES = {".bash_history", ".python_history", ".rhistory", ".zsh_history", ".r_history"}


@dataclass
class RedactResult:
    path: Path
    kind: str = ""
    matches: int = 0
    units: int = 0          # lines / cells / rows changed
    cleared_outputs: int = 0
    skipped: str = ""
    error: str = ""
    written: bool = False
    detail: List[str] = field(default_factory=list)


def mask(text: str, profile: Profile) -> Tuple[str, int]:
    """Replace every in-range identifier match outside URLs; return (text, n)."""
    if not text:
        return text, 0
    spans = [m.span() for m in URL.finditer(text)]
    n = 0

    def sub(m):
        nonlocal n
        if any(a <= m.start() < b for a, b in spans) or profile.allowed(m.group(0)):
            return m.group(0)
        n += 1
        return PLACEHOLDER
    return profile.id_regex.sub(sub, text), n


def _structural(key: str) -> bool:
    k = key.lower()
    return k in SKIP_KEYS or k.endswith("uuid") or k.endswith("_id") or (k.endswith("id") and len(k) <= 12)


def _walk(obj, profile: Profile):
    if isinstance(obj, str):
        return mask(obj, profile)
    if isinstance(obj, list):
        out, n = [], 0
        for v in obj:
            v2, k = _walk(v, profile)
            out.append(v2); n += k
        return out, n
    if isinstance(obj, dict):
        out, n = {}, 0
        for key, v in obj.items():
            if _structural(str(key)):
                out[key] = v
                continue
            v2, k = _walk(v, profile)
            out[key] = v2; n += k
        return out, n
    return obj, 0


def _atomic_write(path: Path, data: bytes) -> None:
    st = path.stat()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".redact-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _kind(path: Path) -> str:
    n = path.name.lower()
    if n.endswith(".jsonl"):
        return "jsonl"
    if n.endswith(".ipynb"):
        return "ipynb"
    if n == "history.sqlite":
        return "ipython_history"
    if path.suffix.lower() in TEXT_EXT or n in TEXT_NAMES:
        return "text"
    return ""


def redact_file(path: Path, profile: Profile, execute: bool = False,
                min_age_min: float = 10.0) -> RedactResult:
    res = RedactResult(path=path, kind=_kind(path))
    if not res.kind:
        res.skipped = "unsupported file type"
        return res
    try:
        age = (time.time() - path.stat().st_mtime) / 60
    except OSError as exc:
        res.error = str(exc)
        return res
    if age < min_age_min:
        res.skipped = f"modified {age:.1f} min ago (< {min_age_min:g}); may be live — not touched"
        return res
    try:
        if res.kind == "jsonl":
            _jsonl(path, profile, res, execute)
        elif res.kind == "ipynb":
            _ipynb(path, profile, res, execute)
        elif res.kind == "ipython_history":
            _sqlite(path, profile, res, execute)
        else:
            raw = path.read_text(errors="surrogateescape")
            new, n = mask(raw, profile)
            res.matches = res.units = n
            if execute and n:
                _atomic_write(path, new.encode(errors="surrogateescape"))
                res.written = True
    except Exception as exc:  # report, never half-write (writes are atomic)
        res.error = f"{type(exc).__name__}: {exc}"
    return res


def _jsonl(path: Path, profile: Profile, res: RedactResult, execute: bool) -> None:
    out: List[bytes] = []
    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8", errors="surrogateescape")
            body = line.rstrip("\n")
            if not profile.id_regex.search(body):
                out.append(raw)
                continue
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                new, n = mask(body, profile)          # not JSON: mask as text
            else:
                obj2, n = _walk(obj, profile)
                new = json.dumps(obj2, ensure_ascii=False, separators=(",", ":")) if n else body
            if n:
                res.matches += n
                res.units += 1
                out.append((new + ("\n" if line.endswith("\n") else "")).encode("utf-8", errors="surrogateescape"))
            else:
                out.append(raw)
    if execute and res.matches:
        _atomic_write(path, b"".join(out))
        res.written = True


def _ipynb(path: Path, profile: Profile, res: RedactResult, execute: bool) -> None:
    nb = json.loads(path.read_text(errors="replace"))
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        joined = "".join(src) if isinstance(src, list) else src
        new, n = mask(joined, profile)
        if n:
            cell["source"] = new.splitlines(keepends=True) if isinstance(src, list) else new
            res.matches += n
            res.units += 1
        if cell.get("cell_type") == "code" and cell.get("outputs"):
            texts = []
            for o in cell["outputs"]:
                texts.append("".join(o.get("text", "")))
                for k, v in (o.get("data") or {}).items():
                    if not k.startswith("image/"):
                        texts.append("".join(v) if isinstance(v, list) else str(v))
                if o.get("output_type") == "error":
                    texts.append("".join(o.get("traceback", [])))
            k = sum(mask(t, profile)[1] for t in texts)
            if k:
                cell["outputs"] = []
                cell["execution_count"] = None
                res.matches += k
                res.cleared_outputs += 1
    if execute and res.matches:
        _atomic_write(path, (json.dumps(nb, indent=1, ensure_ascii=False) + "\n").encode())
        res.written = True


def _sqlite(path: Path, profile: Profile, res: RedactResult, execute: bool) -> None:
    uri = f"file:{path}?mode={'rw' if execute else 'ro'}"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        plan = [(t, c) for t, cols in (("history", ("source", "source_raw")), ("output_history", ("output",)))
                if t in tables for c in cols]
        for table, col in plan:
            rows = con.execute(f"select rowid, {col} from {table}").fetchall()
            for rowid, val in rows:
                if not isinstance(val, str):
                    continue
                new, n = mask(val, profile)
                if n:
                    res.matches += n
                    res.units += 1
                    if execute:
                        con.execute(f"update {table} set {col}=? where rowid=?", (new, rowid))
        if execute and res.matches:
            con.commit()
            con.execute("VACUUM")          # drop freed pages that still hold old values
            res.written = True
    finally:
        con.close()


def iter_targets(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        root = Path(root)
        if root.is_file():
            yield root
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for f in fn:
                p = Path(dp) / f
                if not p.is_symlink() and _kind(p):
                    yield p
