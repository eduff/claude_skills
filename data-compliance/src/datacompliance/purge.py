"""Manifest-driven removal of individual-level data.

Deletion is split into two phases that cannot be collapsed into one command:

1. ``plan``    — writes a manifest describing exactly what would be removed,
                 fingerprinting every file. Nothing is touched.
2. ``execute`` — re-verifies each fingerprint against the disk, then acts.
                 Refuses to run without an explicit confirmation flag.

Safety properties
-----------------
* **Fingerprint verification.** A file whose size, mtime or content digest has
  changed since planning is skipped, not deleted — the plan you reviewed is the
  plan that runs.
* **Quarantine by default.** ``--mode quarantine`` moves files to a holding
  directory so the operation is reversible; ``--mode delete`` unlinks. Both are
  logged identically.
* **Root confinement.** Every target must lie under a root recorded in the
  manifest. Paths outside are refused.
* **No symlink traversal.** Symlinks are never followed and never deleted as
  though they were their targets.
* **Guard paths.** Filesystem root, ``$HOME`` itself and other short paths are
  refused unconditionally.
* **Append-only log.** Every action writes a JSONL record, from which a
  destruction certificate can be produced for audit.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .inventory import Entry, Inventory, human

MANIFEST_VERSION = 1
CHUNK = 64 * 1024

# Paths that may never be a purge target, however they were classified.
GUARD_PATHS = {Path("/"), Path("/home"), Path("/usr"), Path("/etc"), Path("/var"),
               Path("/bin"), Path("/sbin"), Path("/lib"), Path("/boot"), Path("/opt")}
MIN_PATH_DEPTH = 3   # e.g. /mnt/store/user -> depth 3; refuse anything shallower


class PurgeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def fingerprint(path: Path, full: bool = False) -> Dict[str, object]:
    """Cheap, collision-resistant fingerprint.

    Hashes the head and tail of the file plus its size, which distinguishes
    revisions of a multi-GB pickle without reading 25 GB. ``full=True`` digests
    the whole file — correct but slow; use it only when the manifest must stand
    up as evidence.
    """
    st = path.stat()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if full or st.st_size <= 2 * CHUNK:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
            mode = "full"
        else:
            h.update(f.read(CHUNK))
            f.seek(-CHUNK, os.SEEK_END)
            h.update(f.read(CHUNK))
            mode = "head-tail"
    h.update(str(st.st_size).encode())
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": h.hexdigest(), "digest_mode": mode}


def verify(path: Path, fp: Dict[str, object], strict_mtime: bool = True
           ) -> Tuple[bool, str]:
    if not path.exists():
        return False, "gone (already removed?)"
    if path.is_symlink():
        return False, "is a symlink"
    try:
        cur = fingerprint(path, full=(fp.get("digest_mode") == "full"))
    except OSError as exc:
        return False, f"unreadable: {exc}"
    if cur["size"] != fp["size"]:
        return False, f"size changed {fp['size']} -> {cur['size']}"
    if strict_mtime and cur["mtime_ns"] != fp["mtime_ns"]:
        return False, "mtime changed"
    if cur["sha256"] != fp["sha256"]:
        return False, "content digest changed"
    return True, "ok"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Manifest:
    version: int
    created_at: str
    host: str
    user: str
    profile: str
    roots: List[str]
    entries: List[dict] = field(default_factory=list)
    notes: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(e["fingerprint"]["size"] for e in self.entries)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = json.loads(Path(path).read_text())
        if data.get("version") != MANIFEST_VERSION:
            raise PurgeError(f"manifest version {data.get('version')} "
                             f"!= supported {MANIFEST_VERSION}")
        return cls(**data)


def _check_target(path: Path, roots: List[Path]) -> None:
    rp = path.resolve() if not path.is_symlink() else path.absolute()
    if rp in GUARD_PATHS or rp == Path.home():
        raise PurgeError(f"refusing guarded path: {rp}")
    if len(rp.parts) - 1 < MIN_PATH_DEPTH:
        raise PurgeError(f"refusing shallow path (depth < {MIN_PATH_DEPTH}): {rp}")
    if not any(_within(rp, r) for r in roots):
        raise PurgeError(f"target outside declared roots: {rp}")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def plan(inventory: Inventory, profile_name: str,
         dispositions: Iterable[str] = ("PURGE",),
         full_digest: bool = False,
         notes: str = "",
         progress: bool = False) -> Manifest:
    """Build a manifest from an inventory. Reads files; never modifies them."""
    wanted = set(dispositions)
    roots = [Path(r).resolve() for r in inventory.roots]
    man = Manifest(
        version=MANIFEST_VERSION,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        host=socket.gethostname(),
        user=getpass.getuser(),
        profile=profile_name,
        roots=[str(r) for r in roots],
        notes=notes,
    )
    targets = [e for e in inventory.entries
               if e.disposition in wanted and not e.is_symlink]
    for i, e in enumerate(targets, 1):
        if progress and i % 10 == 0:
            print(f"    ... fingerprinted {i}/{len(targets)}", flush=True)
        try:
            _check_target(e.path, roots)
            fp = fingerprint(e.path, full=full_digest)
        except (PurgeError, OSError) as exc:
            man.entries.append({
                "path": str(e.path), "category": e.category, "rule": e.rule,
                "reason": e.reason, "fingerprint": {"size": e.size, "mtime_ns": 0,
                                                    "sha256": "", "digest_mode": "none"},
                "blocked": str(exc),
            })
            continue
        man.entries.append({
            "path": str(e.path), "category": e.category, "rule": e.rule,
            "reason": e.reason, "fingerprint": fp,
        })
    return man


def render_plan(man: Manifest) -> str:
    """Human-readable plan for review before execution."""
    lines = [
        f"# Purge plan — {man.host}",
        "",
        f"- Created: {man.created_at}",
        f"- User: {man.user}",
        f"- Profile: {man.profile}",
        f"- Roots: {', '.join(man.roots)}",
        f"- Targets: **{len(man.entries)} files, {human(man.total_bytes)}**",
    ]
    if man.notes:
        lines += ["", man.notes]
    blocked = [e for e in man.entries if e.get("blocked")]
    if blocked:
        lines += ["", f"## Blocked ({len(blocked)}) — will not be touched", ""]
        for e in blocked:
            lines.append(f"- `{e['path']}` — {e['blocked']}")

    by_cat: Dict[str, List[dict]] = {}
    for e in man.entries:
        if e.get("blocked"):
            continue
        by_cat.setdefault(e["category"], []).append(e)
    for cat, items in sorted(by_cat.items(),
                             key=lambda kv: -sum(i["fingerprint"]["size"] for i in kv[1])):
        size = sum(i["fingerprint"]["size"] for i in items)
        lines += ["", f"## {cat} — {len(items)} files, {human(size)}", "",
                  "| Size | File | Rule |", "|---|---|---|"]
        for e in sorted(items, key=lambda x: -x["fingerprint"]["size"]):
            lines.append(f"| {human(e['fingerprint']['size'])} | `{e['path']}` | {e['rule']} |")
        lines += ["", f"*{items[0]['reason']}*"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@dataclass
class PurgeResult:
    removed: List[Tuple[str, int]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def bytes_removed(self) -> int:
        return sum(s for _, s in self.removed)


def execute(man: Manifest, mode: str = "quarantine",
            quarantine_dir: Optional[Path] = None,
            log_path: Optional[Path] = None,
            strict_mtime: bool = True,
            dry_run: bool = True) -> PurgeResult:
    """Act on a manifest. ``dry_run=True`` (the default) changes nothing."""
    if mode not in ("quarantine", "delete"):
        raise PurgeError(f"unknown mode {mode!r}")
    if mode == "quarantine" and quarantine_dir is None:
        raise PurgeError("quarantine mode requires a quarantine directory")

    roots = [Path(r) for r in man.roots]
    res = PurgeResult()
    log_f = None
    if log_path and not dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a")

    try:
        for e in man.entries:
            path = Path(e["path"])
            if e.get("blocked"):
                res.skipped.append((str(path), f"blocked at plan time: {e['blocked']}"))
                continue
            try:
                _check_target(path, roots)
            except PurgeError as exc:
                res.skipped.append((str(path), str(exc)))
                continue
            ok, why = verify(path, e["fingerprint"], strict_mtime=strict_mtime)
            if not ok:
                res.skipped.append((str(path), why))
                continue
            size = e["fingerprint"]["size"]
            if dry_run:
                res.removed.append((str(path), size))
                continue
            try:
                if mode == "quarantine":
                    dest = _quarantine_dest(path, quarantine_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dest))
                    destination = str(dest)
                else:
                    path.unlink()
                    destination = None
            except OSError as exc:
                res.failed.append((str(path), f"{type(exc).__name__}: {exc}"))
                continue
            res.removed.append((str(path), size))
            if log_f:
                log_f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "host": man.host, "user": man.user, "mode": mode,
                    "path": str(path), "size": size,
                    "sha256": e["fingerprint"]["sha256"],
                    "digest_mode": e["fingerprint"]["digest_mode"],
                    "category": e["category"], "rule": e["rule"],
                    "destination": destination,
                }) + "\n")
                log_f.flush()
    finally:
        if log_f:
            log_f.close()
    return res


def _quarantine_dest(path: Path, quarantine_dir: Path) -> Path:
    """Mirror the absolute path inside the quarantine directory."""
    rel = Path(str(path.resolve()).lstrip("/"))
    return quarantine_dir / rel


def certificate(log_path: Path) -> str:
    """Render a data-destruction record from the append-only log."""
    if not log_path.exists():
        raise PurgeError(f"no log at {log_path}")
    records = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    if not records:
        return "# Data destruction record\n\nNo actions logged.\n"
    total = sum(r["size"] for r in records)
    hosts = sorted({r["host"] for r in records})
    modes = sorted({r["mode"] for r in records})
    first, last = records[0]["ts"], records[-1]["ts"]
    by_cat: Dict[str, List[dict]] = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)

    lines = [
        "# Data destruction record", "",
        f"- Host(s): {', '.join(hosts)}",
        f"- Operator: {', '.join(sorted({r['user'] for r in records}))}",
        f"- Method: {', '.join(modes)}",
        f"- Period: {first} to {last}",
        f"- Total: **{len(records)} files, {human(total)}**", "",
        "## By category", "",
        "| Category | Files | Size |", "|---|---|---|",
    ]
    for cat, items in sorted(by_cat.items(), key=lambda kv: -sum(i["size"] for i in kv[1])):
        lines.append(f"| {cat} | {len(items)} | {human(sum(i['size'] for i in items))} |")
    lines += ["", "## Files", "",
              "| Timestamp | Size | SHA-256 (mode) | Path |", "|---|---|---|---|"]
    for r in records:
        lines.append(f"| {r['ts']} | {human(r['size'])} | `{r['sha256'][:16]}` "
                     f"({r['digest_mode']}) | `{r['path']}` |")
    return "\n".join(lines) + "\n"
