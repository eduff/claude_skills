"""Core file scanners: text, notebook, CSV/TSV, pickle, parquet.

Every scanner returns a :class:`FileReport`. Confidence is derived from hit
density plus surrounding context, which is what keeps the false-positive rate
low on scientific code full of seven-digit sample sizes and p-values.
"""
from __future__ import annotations

import csv
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, Optional, Tuple

from .profile import Profile

CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
ICON = {"HIGH": "\U0001F534", "MEDIUM": "\U0001F7E0", "LOW": "\U0001F7E1", "NONE": "\U0001F7E2"}

# Field-size limit: UKB CSV rows can be enormous.
try:
    csv.field_size_limit(sys.maxsize)
except (OverflowError, ValueError):  # pragma: no cover - platform dependent
    csv.field_size_limit(2 ** 31 - 1)


class TextScan(NamedTuple):
    """Result of scanning a text blob.

    ``raw`` counts every in-range regex match before any context filtering.
    ``total`` counts those that survived it. The two are reported separately so
    that context heuristics can only ever lower confidence, never hide a match
    the authoritative UKB tool would report.
    """
    total: int
    supported: int
    examples: List[str]
    raw: int = 0


@dataclass
class FileReport:
    path: Path
    hits: int = 0
    raw_hits: int = 0
    confidence: str = "NONE"
    kind: str = ""
    note: str = ""
    examples: List[str] = field(default_factory=list)
    error: Optional[str] = None
    size_bytes: int = 0

    @property
    def flagged(self) -> bool:
        return self.confidence in ("HIGH", "MEDIUM")

    @property
    def suppressed(self) -> int:
        """Matches the context filter removed. Non-zero warrants a look."""
        return max(0, self.raw_hits - self.hits)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "hits": self.hits,
            "raw_hits": self.raw_hits,
            "confidence": self.confidence,
            "size_bytes": self.size_bytes,
            "note": self.note,
            "error": self.error,
            "examples": self.examples,
        }


# ---------------------------------------------------------------------------
# Text scanning
# ---------------------------------------------------------------------------

def scan_text(text: str, profile: Profile, window: int = 80, max_examples: int = 5
              ) -> TextScan:
    """Scan a text blob for identifiers."""
    total = 0
    supported = 0
    raw = 0
    examples: List[str] = []
    for m in profile.id_regex.finditer(text):
        try:
            val = int(m.group(m.lastindex or 0))
        except (ValueError, IndexError):
            continue
        if not profile.in_range(val):
            continue
        if profile.allowed(m.group(m.lastindex or 0)):
            continue
        raw += 1
        tail = text[m.end():m.end() + 8]
        # A non-zero decimal tail (1234567.85) is never an identifier.
        if re.match(r"\.\d*[1-9]", tail):
            continue
        lo = max(0, m.start() - window)
        hi = min(len(text), m.end() + window)
        snippet = text[lo:hi]
        if profile.context_excludes(snippet):
            continue
        supports = profile.context_supports(snippet)
        # A ".0" tail is ambiguous: pandas renders an integer ID column as float
        # whenever NaNs are present, but result tables are full of such floats.
        # Count it only when the surrounding context names an identifier.
        if re.match(r"\.0+(?![0-9])", tail) and not supports:
            continue
        total += 1
        if supports:
            supported += 1
        if len(examples) < max_examples:
            examples.append(re.sub(r"\s+", " ", snippet).strip()[:160])
    return TextScan(total, supported, examples, raw)


def classify(total: int, supported: int, kind: str) -> str:
    if total == 0:
        return "NONE"
    if kind in ("csv", "pkl", "parquet"):
        return "HIGH"  # column-mode hits are unambiguous
    if supported >= 3 or total >= 50:
        return "HIGH"
    if supported >= 1 or total >= 5:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Per-format scanners
# ---------------------------------------------------------------------------

def scan_text_file(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    r = FileReport(path=path, kind="text", size_bytes=_size(path))
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        r.error = f"{type(exc).__name__}: {exc}"
        return r
    res = scan_text(text, profile, max_examples=sample)
    r.hits, r.raw_hits = res.total, res.raw
    r.examples = res.examples[:sample]
    r.confidence = classify(res.total, res.supported, r.kind)
    return r


def scan_csv(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    r = FileReport(path=path, kind="csv", size_bytes=_size(path))
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with open(path, "r", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            try:
                header = next(reader)
            except StopIteration:
                r.note = "empty file"
                return r
            id_cols = [(i, h) for i, h in enumerate(header) if profile.is_id_column(h)]
            if not id_cols:
                # Safety net: bounded text scan of the header plus a row sample.
                lines = [delim.join(header)]
                for _, row in zip(range(profile.csv_sample_rows), reader):
                    lines.append(delim.join(row))
                res = scan_text("\n".join(lines), profile, max_examples=sample)
                r.hits, r.raw_hits = res.total, res.raw
                r.examples = res.examples[:sample]
                r.confidence = classify(res.total, res.supported, "text")
                r.note = f"no {profile.id_label} column; sampled {profile.csv_sample_rows} rows"
                return r
            count = 0
            examples: List[str] = []
            for row in reader:
                for ci, _ in id_cols:
                    if ci < len(row):
                        cell = row[ci].strip()
                        if cell.isdigit() and profile.in_range(int(cell)):
                            count += 1
                            if len(examples) < sample:
                                examples.append(f"{header[ci]}=<redacted {profile.id_label}>")
            r.hits = count
            r.examples = examples
            r.note = f"{profile.id_label} column(s): {[h for _, h in id_cols]}"
            r.confidence = "HIGH" if count else "NONE"
    except OSError as exc:
        r.error = f"{type(exc).__name__}: {exc}"
    return r


def notebook_output_text(cell: dict) -> List[str]:
    """Collect all text emitted by a code cell's outputs (never its source)."""
    out: List[str] = []
    for o in cell.get("outputs", []):
        t = o.get("text")
        if t is not None:
            out.extend(t if isinstance(t, list) else [t])
        for v in (o.get("data") or {}).values():
            if isinstance(v, list):
                out.extend(str(x) for x in v)
            elif isinstance(v, str):
                out.append(v)
    return out


def notebook_source_text(cell: dict) -> str:
    """A code cell's source. Hardcoded identifiers live here — e.g.
    ``a='7000002'`` or ``data.loc[[7000003], :]`` — and they are as much an
    exposure as a printed output, just with a different fix: edit the code."""
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else str(src)


def scan_notebook(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    """Scan a notebook's cell outputs **and** its source.

    Outputs are cleared by `notebooks clean`; source hits are not — they are
    hardcoded identifiers that a human must remove. They are counted and
    labelled separately so the right remedy is obvious.
    """
    r = FileReport(path=path, kind="notebook", size_bytes=_size(path))
    try:
        with open(path, "r", errors="replace") as f:
            nb = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        r.error = f"{type(exc).__name__}: {exc}"
        return r

    out_total = out_sup = out_cells = out_raw = 0
    src_total = src_sup = src_cells = src_raw = 0
    examples: List[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") not in ("code", "markdown"):
            continue
        if cell.get("cell_type") == "code":
            blob = "".join(notebook_output_text(cell))
            if blob:
                res = scan_text(blob, profile, max_examples=sample)
                out_raw += res.raw
                if res.total:
                    out_total += res.total
                    out_sup += res.supported
                    out_cells += 1
                    for e in res.examples:
                        if len(examples) < sample:
                            examples.append(e)
        src = notebook_source_text(cell)
        if src:
            res = scan_text(src, profile, max_examples=sample)
            src_raw += res.raw
            if res.total:
                src_total += res.total
                src_sup += res.supported
                src_cells += 1
                for e in res.examples:
                    if len(examples) < sample:
                        examples.append(f"[SOURCE] {e}")

    r.hits = out_total + src_total
    r.raw_hits = out_raw + src_raw
    r.examples = examples
    parts = []
    if out_total:
        parts.append(f"{out_total} in outputs ({out_cells} cell(s))")
    if src_total:
        parts.append(f"{src_total} HARDCODED IN SOURCE ({src_cells} cell(s)) "
                     f"— clearing outputs will NOT remove these")
    r.note = "; ".join(parts) if parts else "0 cell(s) with hits"
    r.confidence = classify(r.hits, out_sup + src_sup, "notebook")
    # A hardcoded identifier in source is unambiguous: it was typed, not printed.
    if src_total:
        r.confidence = "HIGH"
    return r


def scan_pkl(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    """Inspect a pickle for ID columns. Large pickles are assumed bulk data and skipped."""
    r = FileReport(path=path, kind="pkl", size_bytes=_size(path))
    size_mb = r.size_bytes / 1024 / 1024
    if size_mb > profile.max_pkl_mb:
        r.kind = "skip"
        r.note = f"not opened: {size_mb:.0f} MB > cap {profile.max_pkl_mb:.0f} MB (assume bulk data)"
        return r
    try:
        import pickle
        with open(path, "rb") as f:
            obj = pickle.load(f)
    except Exception as exc:  # unpickling can raise almost anything
        r.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return r
    try:
        import pandas as pd
    except ImportError:
        r.note = "pandas unavailable; pickle not inspected"
        return r
    if not isinstance(obj, pd.DataFrame):
        r.note = f"pickle holds {type(obj).__name__}; no DataFrame inspection"
        return r

    idx_name = obj.index.name
    id_in_index = bool(idx_name and profile.is_id_column(str(idx_name)))
    id_cols = [c for c in obj.columns if profile.is_id_column(str(c))]
    total = 0
    examples: List[str] = []
    if id_in_index:
        n = _count_in_range(obj.index, profile)
        total += n
        if n:
            examples.append(f"index({idx_name}): {n} in-range values")
    for c in id_cols:
        n = _count_in_range(obj[c].dropna(), profile)
        total += n
        if n and len(examples) < sample:
            examples.append(f"column {c!r}: {n} in-range values")
    r.hits = total
    r.examples = examples
    r.note = (f"DataFrame {obj.shape}; {profile.id_label} cols={id_cols}; "
              f"{profile.id_label} index={idx_name if id_in_index else None}")
    r.confidence = "HIGH" if total else "NONE"
    return r


class _Walker:
    """Recursively inspect an unpickled object for participant-level content.

    Flags identifier-named or identifier-range index/columns (as scan_pkl does,
    but also inside dicts, lists and model objects) and any table, array or
    sequence long enough to be one row per participant or person-interval.
    Fitted model objects are walked too: lifelines / statsmodels fitters keep
    durations, design matrices and residuals per row.
    """

    def __init__(self, profile: Profile):
        self.p = profile
        self.ids = 0
        self.max_rows = 0
        self.why: List[str] = []
        self._seen: set = set()

    def _note(self, s: str) -> None:
        if len(self.why) < 3:
            self.why.append(s)

    def _id_range(self, values) -> int:
        """Count values in the identifier range when they look like an ID column
        (integer-valued, near-unique). Sample sizes and statistics fail this."""
        try:
            import pandas as pd
            v = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
        except Exception:
            return 0
        if len(v) < 20 or (v % 1 != 0).mean() > 0.01:
            return 0
        inr = v[(v >= self.p.id_min) & (v <= self.p.id_max)]
        if len(inr) < 20 or len(inr) < 0.5 * len(v) or inr.nunique() < 0.9 * len(inr):
            return 0
        return int(len(inr))

    def frame(self, df, where: str) -> None:
        import pandas as pd
        n = len(df)
        self.max_rows = max(self.max_rows, n)
        for name in [df.index.name] + list(df.columns):
            if name is not None and self.p.is_id_column(str(name)):
                self.ids += n
                self._note(f"{where}: {self.p.id_label}-named field {str(name)[:30]!r}")
        if pd.api.types.is_numeric_dtype(df.index):
            k = self._id_range(df.index)
            if k:
                self.ids += k
                self._note(f"{where}: {self.p.id_label}-range index ({k} values)")
        for c in list(df.columns)[:400]:
            col = df[c]
            if isinstance(col, pd.DataFrame) or not pd.api.types.is_numeric_dtype(col):
                continue
            k = self._id_range(col)
            if k:
                self.ids += k
                self._note(f"{where}: {self.p.id_label}-range column {str(c)[:30]!r} ({k} values)")
        if n >= self.p.individual_rows:
            self._note(f"{where}: {n} rows x {df.shape[1]} cols")

    def walk(self, o, where: str = "obj", depth: int = 0) -> None:
        if depth > 6 or id(o) in self._seen:
            return
        self._seen.add(id(o))
        try:
            import numpy as np
            import pandas as pd
        except ImportError:  # pragma: no cover - guarded by the caller
            return
        if isinstance(o, pd.DataFrame):
            self.frame(o, where)
        elif isinstance(o, pd.Series):
            self.frame(o.to_frame(), where)
        elif isinstance(o, np.ndarray):
            n = o.shape[0] if o.ndim else 0
            self.max_rows = max(self.max_rows, n)
            if n >= self.p.individual_rows:
                self._note(f"{where}: array {o.shape}")
            if o.ndim == 1 and n >= 20 and np.issubdtype(o.dtype, np.number):
                k = self._id_range(o)
                if k:
                    self.ids += k
                    self._note(f"{where}: {self.p.id_label}-range array ({k})")
        elif isinstance(o, dict):
            for k, v in list(o.items())[:2000]:
                self.walk(v, f"{where}[{str(k)[:25]}]", depth + 1)
        elif isinstance(o, (list, tuple, set, frozenset)):
            seq = list(o)
            if len(seq) >= self.p.individual_rows:
                self.max_rows = max(self.max_rows, len(seq))
                self._note(f"{where}: sequence of {len(seq)}")
                if all(isinstance(x, int) for x in seq[:200]):
                    k = self._id_range(seq)
                    if k:
                        self.ids += k
            for i, v in enumerate(seq[:200]):
                self.walk(v, f"{where}[{i}]", depth + 1)
        elif hasattr(o, "__dict__") and not isinstance(o, type):
            for k, v in list(vars(o).items())[:200]:
                self.walk(v, f"{where}.{k}", depth + 1)


def content_verdict(path: Path, profile: Profile) -> Tuple[str, str]:
    """Is this data file aggregate, individual-level, or unverifiable?

    Returns (verdict, reason) with verdict one of ``aggregate``, ``individual``
    or ``unverified``. Used to stop a path- or name-based KEEP rule from keeping
    a per-participant file that happens to sit in a results folder. Anything that
    cannot be opened and checked is ``unverified`` — callers must treat that as
    not safe to keep, never as aggregate.
    """
    ext = path.suffix.lower()
    size_mb = _size(path) / 1024 / 1024
    if ext in (".pkl", ".pickle"):
        if size_mb > profile.max_pkl_mb:
            return "unverified", f"{size_mb:.0f} MB > cap {profile.max_pkl_mb:.0f} MB, not opened"
        try:
            import numpy  # noqa: F401
            import pandas  # noqa: F401
        except ImportError:
            return "unverified", "pandas unavailable; pickle not inspected"
        try:
            import pickle
            with open(path, "rb") as f:
                obj = pickle.load(f)
        except Exception as exc:  # unpickling can raise almost anything
            return "unverified", f"unreadable: {type(exc).__name__}"
        w = _Walker(profile)
        w.walk(obj)
        if w.ids:
            return "individual", "; ".join(w.why) or f"{profile.id_label} values present"
        if w.max_rows >= profile.individual_rows:
            return "individual", "; ".join(w.why) or f"{w.max_rows} rows"
        return "aggregate", f"largest table / array {w.max_rows} rows; no {profile.id_label} fields"
    if ext in (".csv", ".tsv"):
        r = scan_csv(path, profile)
        if r.error:
            return "unverified", r.error
        if r.hits and r.confidence in ("HIGH", "MEDIUM"):
            return "individual", r.note
        try:
            with open(path, "rb") as f:
                rows = sum(1 for _ in f) - 1
        except OSError as exc:
            return "unverified", f"{type(exc).__name__}"
        if rows >= profile.individual_rows:
            return "individual", f"{rows} rows"
        return "aggregate", f"{rows} rows; no {profile.id_label} column"
    if ext == ".parquet":
        try:
            import pyarrow.parquet as pq
            meta = pq.ParquetFile(str(path)).metadata
            names = pq.read_schema(str(path)).names
        except Exception as exc:
            return "unverified", f"cannot read parquet metadata: {type(exc).__name__}"
        idc = [n for n in names if profile.is_id_column(n)]
        if idc:
            return "individual", f"{profile.id_label} column(s) {idc}"
        if meta.num_rows >= profile.individual_rows:
            return "individual", f"{meta.num_rows} rows"
        return "aggregate", f"{meta.num_rows} rows; no {profile.id_label} column"
    if ext in TEXT_CHECK_EXT:
        return text_id_verdict(path, profile)
    return "unverified", f"no content check for {ext or 'no extension'}"


# Text formats the KEEP content check can read. A .txt is as likely to be a
# list of participant IDs as a README, so "documentation" must be verified too.
TEXT_CHECK_EXT = (".txt", ".md", ".rst", ".qmd", ".tex", ".bib", ".log", ".docx")


def text_id_verdict(path: Path, profile: Profile) -> Tuple[str, str]:
    """Is this text file an identifier list or per-participant dump?

    Individual when it holds at least ``id_list_min`` distinct in-range
    identifier values on a fifth or more of its non-empty lines (an ID list, a
    printed ID column), or 1,000 or more distinct values anywhere. Prose that
    mentions a few sample sizes stays aggregate.
    """
    size_mb = _size(path) / 1024 / 1024
    if size_mb > profile.max_text_mb:
        return "unverified", f"{size_mb:.0f} MB > cap {profile.max_text_mb:.0f} MB, not read"
    try:
        if path.suffix.lower() == ".docx":
            import zipfile
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            text = re.sub(r"</w:p>", "\n", xml)
            text = re.sub(r"<[^>]+>", "", text)
        else:
            text = path.read_text(errors="replace")
    except Exception as exc:  # bad zip, missing part, I/O
        return "unverified", f"unreadable: {type(exc).__name__}"
    values, lines_hit, keyed = set(), 0, 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        hit = False
        for m in profile.id_regex.finditer(ln):
            v = m.group()
            if profile.allowed(v) or not profile.in_range(int(v)):
                continue
            values.add(v)
            hit = True
            if not ln[:m.start()].strip():
                keyed += 1
        lines_hit += hit
    n = len(values)
    share = lines_hit / len(lines) if lines else 0.0
    if n >= 1000 or (n >= profile.id_list_min and share >= 0.2):
        return "individual", f"{n} distinct {profile.id_label} values on {share:.0%} of lines"
    # Rows keyed by an identifier (a small per-participant table or batch list):
    # most lines open with one. Prose almost never starts a line with one.
    if keyed >= 2 and keyed >= 0.5 * len(lines):
        return "individual", f"{keyed} of {len(lines)} lines start with a {profile.id_label} value"
    return "aggregate", f"text; {n} distinct {profile.id_label}-range values"


def scan_parquet(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    r = FileReport(path=path, kind="parquet", size_bytes=_size(path))
    try:
        import pyarrow.parquet as pq
    except ImportError:
        r.kind = "skip"
        r.note = "pyarrow not installed; not inspected"
        return r
    try:
        pf = pq.ParquetFile(path)
        cols = list(pf.schema_arrow.names)
    except Exception as exc:
        r.error = f"{type(exc).__name__}: {exc}"
        return r
    id_cols = [c for c in cols if profile.is_id_column(str(c))]
    if not id_cols:
        r.note = f"no {profile.id_label} columns (of {len(cols)})"
        return r
    total = 0
    examples: List[str] = []
    try:
        tbl = pf.read(columns=id_cols)
        for c in id_cols:
            n = sum(1 for v in tbl[c].to_pylist()
                    if isinstance(v, int) and profile.in_range(v))
            total += n
            if n and len(examples) < sample:
                examples.append(f"column {c!r}: {n} in-range values")
    except Exception as exc:
        r.error = f"{type(exc).__name__}: {exc}"
        return r
    r.hits = total
    r.examples = examples
    r.note = f"{profile.id_label} columns: {id_cols}"
    r.confidence = "HIGH" if total else "NONE"
    return r


def _count_in_range(values, profile: Profile) -> int:
    try:
        ints = values.astype("int64", copy=False)
    except (TypeError, ValueError, OverflowError):
        return 0
    return int(((ints >= profile.id_min) & (ints <= profile.id_max)).sum())


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Dispatch and traversal
# ---------------------------------------------------------------------------

def name_hits(path: Path, profile: Profile) -> int:
    """Identifiers in the *filename*. UKB bulk downloads are named
    ``<eid>_<field>_<instance>_<array>.<ext>``, so the name alone leaks."""
    return sum(1 for m in profile.id_regex.finditer(path.name)
               if profile.in_range(int(m.group(m.lastindex or 0))))


def _with_name_hits(r: FileReport, nh: int) -> FileReport:
    if nh:
        r.hits += nh
        r.raw_hits += nh
        r.confidence = "HIGH"
        r.note = (r.note + "; " if r.note else "") + f"identifier in FILENAME ({nh})"
    return r


def dispatch(path: Path, profile: Profile, sample: int = 5) -> FileReport:
    ext = path.suffix.lower()
    nh = name_hits(path, profile)
    if path.name in profile.skip_names or ext in profile.skip_ext:
        r = FileReport(path=path, kind="skip", note="skipped by rule", size_bytes=_size(path))
        if nh:
            # Content is skipped, but a leaking filename still counts.
            r.hits = r.raw_hits = nh
            r.confidence = "HIGH"
            r.note = f"identifier in FILENAME ({nh}); content not scanned"
        return r
    if ext in (".csv", ".tsv"):
        return _with_name_hits(scan_csv(path, profile, sample), nh)
    if ext == ".ipynb":
        return _with_name_hits(scan_notebook(path, profile, sample), nh)
    if ext in (".pkl", ".pickle"):
        return _with_name_hits(scan_pkl(path, profile, sample), nh)
    if ext == ".parquet":
        return _with_name_hits(scan_parquet(path, profile, sample), nh)
    size = _size(path)
    if size > profile.max_text_mb * 1024 * 1024:
        r = FileReport(path=path, kind="skip", size_bytes=size,
                       note=f"not read: {size / 1024 / 1024:.0f} MB text file")
    else:
        r = scan_text_file(path, profile, sample)
    if nh:
        r.hits += nh
        r.raw_hits += nh
        r.confidence = "HIGH"
        r.note = (r.note + "; " if r.note else "") + f"identifier in FILENAME ({nh})"
    return r


def walk(roots: Iterable[Path], profile: Profile, recursive: bool = True,
         follow_symlinks: bool = False, skip_vendor: bool = False) -> Iterator[Path]:
    """Yield files under *roots*, pruning profile-listed directories.

    ``skip_vendor`` additionally prunes installed dependencies. Source scans
    want this; the inventory does not, because every byte needs a disposition.
    """
    import os

    for root in roots:
        if not root.exists():
            print(f"  ! {root}: not found", file=sys.stderr)
            continue
        if root.is_file():
            yield root
            continue
        if not recursive:
            for p in sorted(root.iterdir()):
                if p.is_file():
                    yield p
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
            keep = []
            for d in sorted(dirnames):
                if d in profile.skip_dirs:
                    continue
                # Vendor entries may be globs (".vscode-server*"), so that a
                # renamed or suffixed copy is pruned too.
                if skip_vendor and any(fnmatch.fnmatch(d, pat)
                                       for pat in profile.vendor_dirs):
                    continue
                keep.append(d)
            dirnames[:] = keep
            for fn in sorted(filenames):
                yield Path(dirpath) / fn
