"""Generate a compliance audit document from a live scan.

Replaces the hand-maintained audit markdown that goes stale the moment the
tree changes. Every number here is produced by the run that wrote the file.
"""
from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Dict, List, Optional

from .gitaudit import GitAudit
from .inventory import Inventory, human
from .paths import PathReport, summarise

BADGE = {"KEEP": "\U0001F7E2", "REVIEW": "\U0001F7E1", "PURGE": "\U0001F534"}
SEV = {"HIGH": "\U0001F534", "MEDIUM": "\U0001F7E0", "LOW": "\U0001F7E1"}


def build_report(inv: Inventory, profile_name: str,
                 git: Optional[GitAudit] = None,
                 path_reports: Optional[List[PathReport]] = None,
                 title: str = "Data Compliance Audit",
                 top_n: int = 40) -> str:
    L: List[str] = []
    now = time.strftime("%Y-%m-%d %H:%M %Z")
    L += [f"# {title}", "",
          f"*Generated {now} on `{socket.gethostname()}` by `data-compliance` "
          f"(profile: `{profile_name}`). Do not edit by hand — regenerate.*", ""]

    # ---- headline ---------------------------------------------------------
    totals = inv.totals()
    L += ["## Summary", "",
          "| Disposition | Files | Size |", "|---|---:|---:|"]
    for disp in ("PURGE", "REVIEW", "KEEP"):
        t = totals.get(disp)
        if t:
            L.append(f"| {BADGE[disp]} {disp} | {int(t['files']):,} | "
                     f"{human(t['bytes'])} |")
    L += ["", f"Roots scanned: {', '.join(f'`{r}`' for r in inv.roots)}", ""]
    if inv.errors:
        L += [f"{len(inv.errors)} path(s) could not be read; see `--json` output.", ""]

    # ---- git exposure -----------------------------------------------------
    if git is not None:
        L += ["---", "", "## Git exposure", ""]
        r = git.remote
        if r.has_remote:
            dests = ", ".join(f"`{u}`" for u in r.remotes.values())
            state = ("**pushed** — history is on the remote" if r.fully_pushed
                     else f"{r.ahead} commit(s) not yet pushed")
            L += [f"Remote: {dests} — {state}.", ""]
        else:
            L += ["No remote configured — history is local only.", ""]
        L.append(f"Tracked files: {git.n_tracked}")
        L.append("")
        if git.tracked_bulk:
            L += ["### Bulk data committed to the repo", "",
                  "| Size | Path |", "|---:|---|"]
            for rel, size in git.tracked_bulk:
                L.append(f"| {human(size)} | `{rel}` |")
            L.append("")
        if git.tracked_flagged:
            L += ["### Identifiers in tracked working-tree files", "",
                  "| Hits | Confidence | Path |", "|---:|---|---|"]
            for f in git.tracked_flagged:
                L.append(f"| {f.hits:,} | {f.confidence} | `{f.path}` |")
            L.append("")
        if git.history_hits:
            L += ["### Identifiers in git *history*", "",
                  "Present in past commits even where the working tree is clean. "
                  "Clearing the file now does not remove it from history.", "",
                  "| Worst hits | Versions | First seen | Path |", "|---:|---:|---|---|"]
            for h in git.history_hits:
                L.append(f"| {h.hits:,} | {h.n_versions} | {h.first_commit} | `{h.path}` |")
            L.append("")
        if git.clean:
            L += ["No identifiers or bulk data found in tracked files or history.", ""]

    # ---- inventory detail -------------------------------------------------
    L += ["---", "", "## Inventory by category", "",
          "| Category | Disposition | Files | Size |", "|---|---|---:|---:|"]
    for cat, t in sorted(inv.by_category().items(), key=lambda kv: -kv[1]["bytes"]):
        L.append(f"| {cat} | {BADGE.get(str(t['disposition']), '')} {t['disposition']} "
                 f"| {int(t['files']):,} | {human(t['bytes'])} |")
    L.append("")

    for disp in ("PURGE", "REVIEW"):
        items = inv.by_disposition(disp)
        if not items:
            continue
        shown = items[:top_n]
        L += [f"### {BADGE[disp]} {disp} — largest {len(shown)} of {len(items)}", "",
              "| Size | Path | Rule |", "|---:|---|---|"]
        for e in shown:
            L.append(f"| {human(e.size)} | `{e.path}` | {e.rule or '—'} |")
        L.append("")
        reasons: Dict[str, str] = {}
        for e in items:
            if e.rule and e.rule not in reasons:
                reasons[e.rule] = e.reason
        if reasons:
            L += ["Rules applied:", ""]
            for rule, reason in sorted(reasons.items()):
                L.append(f"- `{rule}` — {reason}")
            L.append("")

    # ---- path leakage -----------------------------------------------------
    if path_reports:
        agg = summarise(path_reports)
        L += ["---", "", "## Server-identifying paths and credentials", "",
              "Not participant data, but blocks public or cross-site release.", "",
              "| Severity | Pattern | Occurrences | Files |", "|---|---|---:|---:|"]
        for name, e in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            L.append(f"| {SEV.get(str(e['severity']), '')} {e['severity']} | `{name}` "
                     f"| {e['total']:,} | {len(e['files'])} |")
        L.append("")
        for name, e in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            strings = sorted(e["strings"].items(), key=lambda kv: -kv[1])[:8]
            L += [f"**`{name}`** — {e['hint']}", ""]
            for s, n in strings:
                L.append(f"- `{s}` × {n:,}")
            L.append("")

    L += ["---", "", "## Next steps", "",
          "1. Review the PURGE list above, then `data-compliance purge plan` to "
          "produce a manifest and `purge execute` to act on it.",
          "2. Resolve every REVIEW entry — nothing is assumed safe.",
          "3. If git history shows identifiers, rewrite it before any wider release.",
          "4. Re-run this report to confirm; it is the record of what was done.", ""]
    return "\n".join(L)
