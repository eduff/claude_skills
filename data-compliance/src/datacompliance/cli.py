"""Command-line interface.

    data-compliance scan       PATH...      identifiers in files
    data-compliance paths      PATH...      server paths / credentials in source
    data-compliance inventory  PATH...      classify files, propose dispositions
    data-compliance provenance PATH...      can PURGE targets be regenerated?
    data-compliance git-audit  [REPO]       tracked + history exposure
    data-compliance report     PATH...      full audit document
    data-compliance notebooks  MODE FILE... audit / clean / strip outputs
    data-compliance purge      plan|execute|certificate
    data-compliance install-hook [REPO]     pre-commit guard
    data-compliance rewrite-history REPO    guarded git-filter-repo wrapper
"""
from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path
from typing import List

from . import (gitaudit, inventory, paths as pathscan, provenance as prov,
               purge as purgemod, remediate, report)
from .profile import ProfileError, available_profiles, load_profile
from .scan import CONFIDENCE_ORDER, ICON, dispatch, walk

TOOL = "data-compliance"


class Progress:
    """Live counter on stderr, so it never contaminates --json on stdout.

    Overwrites one line on a terminal; on a pipe or in a log it prints a
    periodic line instead, so redirected output stays readable.
    """

    def __init__(self, enabled: bool, label: str = "scanned"):
        self.enabled = enabled
        self.label = label
        self.n = 0
        self.bytes = 0
        self.hits = 0
        self.start = time.time()
        self.last = 0.0
        self.tty = sys.stderr.isatty()

    def update(self, path=None, size: int = 0, hits: int = 0) -> None:
        if not self.enabled:
            return
        self.n += 1
        self.bytes += size
        self.hits += hits
        now = time.time()
        if now - self.last < (0.2 if self.tty else 5.0):
            return
        self.last = now
        self._draw(path)

    def _draw(self, path=None) -> None:
        rate = self.n / max(1e-6, time.time() - self.start)
        msg = (f"  {self.n:,} {self.label}  {_human(self.bytes)}  "
               f"{rate:,.0f}/s  {self.hits:,} hit(s)")
        if self.tty:
            tail = ""
            if path is not None:
                name = str(path)
                width = max(0, min(60, 110 - len(msg)))
                tail = "  " + (name[-width:] if len(name) > width else name)
            print(f"\r\033[K{msg}{tail}", end="", file=sys.stderr, flush=True)
        else:
            print(msg, file=sys.stderr, flush=True)

    def done(self) -> None:
        if not self.enabled:
            return
        self._draw()
        if self.tty:
            print(file=sys.stderr)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _profile(args):
    root = Path(args.paths[0]) if getattr(args, "paths", None) else Path.cwd()
    return load_profile(args.profile, project_root=root)


def _paths(args) -> List[Path]:
    return [Path(p) for p in args.paths]


# ---------------------------------------------------------------------------

def cmd_scan(args) -> int:
    prof = _profile(args)
    prog = Progress(getattr(args, "progress", False), "files scanned")
    reports = []
    for p in walk(_paths(args), prof, recursive=not args.no_recursive):
        r = dispatch(p, prof, args.show)
        reports.append(r)
        prog.update(p, r.size_bytes, r.hits)
    prog.done()

    def show(r):
        if r.error:
            return not args.quiet
        if r.confidence == "NONE":
            return args.include_clean
        if r.confidence == "LOW":
            return args.include_low and r.hits >= args.min_hits
        return r.hits >= args.min_hits

    shown = [r for r in reports if show(r)]
    if args.json:
        print(json.dumps([r.as_dict() for r in shown], indent=2))
    elif not args.quiet:
        shown.sort(key=lambda r: (CONFIDENCE_ORDER.get(r.confidence, 9), -r.hits, str(r.path)))
        for r in shown:
            tail = f"  [{r.note}]" if r.note else ""
            err = f"  ERROR: {r.error}" if r.error else ""
            sup = (f"  (+{r.suppressed:,} filtered as false positives)"
                   if r.suppressed else "")
            print(f"{ICON.get(r.confidence,'?')} {r.confidence:<6} {r.hits:>8,}  "
                  f"{r.path}{tail}{sup}{err}")
            for ex in r.examples[:args.show]:
                print(f"         · {ex}")
        n = {c: sum(1 for r in reports if r.confidence == c)
             for c in ("HIGH", "MEDIUM", "LOW", "NONE")}
        supp = sum(r.suppressed for r in reports)
        extra = (f"  raw matches={sum(r.raw_hits for r in reports):,} "
                 f"({supp:,} filtered as false positives)") if supp else ""
        print(f"\nScanned {len(reports)} files  (HIGH={n['HIGH']} MED={n['MEDIUM']} "
              f"LOW={n['LOW']} CLEAN={n['NONE']})  total hits="
              f"{sum(r.hits for r in reports):,}{extra}", file=sys.stderr)
    return 1 if any(r.flagged for r in reports) else 0


def cmd_paths(args) -> int:
    prof = _profile(args)
    prog = Progress(getattr(args, "progress", False), "files scanned")
    reps = pathscan.scan_paths(_paths(args), prof, recursive=not args.no_recursive,
                               max_examples=args.show,
                               on_file=lambda p, n: prog.update(p, 0, n))
    prog.done()
    agg = pathscan.summarise(reps)
    if args.json:
        print(json.dumps({k: {**v, "files": sorted(v["files"])} for k, v in agg.items()},
                         indent=2, default=str))
    else:
        for name, e in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            print(f"{report.SEV.get(str(e['severity']),' ')} {e['severity']:<7} "
                  f"{name:<24} {e['total']:>7,} in {len(e['files']):>4} files")
            if e["hint"]:
                print(f"        {e['hint']}")
            for s, n in sorted(e["strings"].items(), key=lambda kv: -kv[1])[:args.show]:
                print(f"        {n:>6,}x  {s}")
        if args.filenames:
            flagged = pathscan.check_filenames(_paths(args), prof,
                                               recursive=not args.no_recursive)
            if flagged:
                print("\nSensitive filenames:")
                for f, sev, note in flagged:
                    print(f"  {sev:<7} {f}  — {note}")
    return 1 if any(r.worst in ("HIGH", "MEDIUM") for r in reps) else 0


def cmd_inventory(args) -> int:
    prof = _profile(args)
    inv = inventory.build(_paths(args), prof, min_report_mb=args.min_mb,
                          progress=args.progress,
                          skip_vendor=not getattr(args, "include_vendor", False))
    if args.json:
        print(json.dumps({"roots": [str(r) for r in inv.roots],
                          "entries": [e.as_dict() for e in inv.entries],
                          "duplicates_skipped": len(inv.duplicates),
                          "errors": inv.errors}, indent=2))
        return 0
    for disp in ("PURGE", "REVIEW", "KEEP"):
        t = inv.totals().get(disp)
        if t:
            print(f"{report.BADGE[disp]} {disp:<7} {int(t['files']):>7,} files  "
                  f"{inventory.human(t['bytes']):>10}")
    print()
    for cat, t in sorted(inv.by_category().items(), key=lambda kv: -kv[1]["bytes"]):
        print(f"  {cat:<24} {str(t['disposition']):<7} {int(t['files']):>6,}  "
              f"{inventory.human(t['bytes']):>10}")
    if args.list:
        for disp in args.list:
            items = inv.by_disposition(disp.upper())
            print(f"\n--- {disp.upper()} ({len(items)}) ---")
            for e in items[:args.top]:
                print(f"  {inventory.human(e.size):>10}  {e.rule or '-':<28} {e.path}")
    if inv.vendor_dirs_skipped:
        print(f"\n{len(inv.vendor_dirs_skipped)} dependency tree(s) not classified "
              f"({inventory.human(inv.vendor_bytes_skipped)}); "
              f"pass --include-vendor to classify them", file=sys.stderr)
    if inv.duplicates:
        print(f"\n{len(inv.duplicates):,} path(s) skipped as hardlinks or "
              f"symlinked-root duplicates of files already counted", file=sys.stderr)
    if inv.errors:
        print(f"{len(inv.errors)} unreadable path(s)", file=sys.stderr)
    return 0


def cmd_provenance(args) -> int:
    prof = _profile(args)
    roots = _paths(args)
    inv = inventory.build(roots, prof, min_report_mb=args.min_mb)
    targets = [e.path for e in inv.entries
               if e.disposition in {d.upper() for d in args.disposition}]
    if not targets:
        print("no matching targets", file=sys.stderr)
        return 0
    tracked = None
    code_roots = [Path(r) for r in (args.code or [str(x) for x in roots])]
    repo = Path(args.repo) if args.repo else roots[0]
    if gitaudit.is_git_repo(repo):
        tracked = gitaudit.tracked_files(gitaudit.repo_root(repo))
    purge_names = {t.name for t in targets}
    results = prov.analyse(targets, code_roots, prof, tracked=tracked,
                           purge_names=purge_names)
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
        return 0
    order = ["ORPHAN", "NO_GENERATOR", "GENERATOR_COMMENTED_OUT",
             "GENERATOR_UNTRACKED", "CHAIN_AT_RISK", "REGENERABLE"]
    mark = {"REGENERABLE": "OK  ", "GENERATOR_UNTRACKED": "WARN",
            "CHAIN_AT_RISK": "WARN", "NO_GENERATOR": "STOP",
            "GENERATOR_COMMENTED_OUT": "STOP", "ORPHAN": "??  "}
    for verdict in order:
        group = [r for r in results if r.verdict == verdict]
        if not group:
            continue
        print(f"\n=== {verdict} ({len(group)}) — {prov.VERDICTS[verdict]} ===")
        for r in group:
            print(f"  [{mark[verdict]}] {r.target.name:<34} {r.detail}")
            for w in r.writers[:args.show]:
                t = "tracked" if w.tracked else "untracked"
                print(f"          writer ({t}): {w}")
            for w in r.commented_writers[:args.show]:
                print(f"          COMMENTED-OUT writer: {w}")
            if r.upstream:
                print(f"          upstream also being removed: {', '.join(r.upstream)}")
            if verdict in ("NO_GENERATOR", "ORPHAN") and r.readers:
                print(f"          read by: {r.readers[0].file}"
                      f" (+{len(r.readers)-1} more)" if len(r.readers) > 1 else
                      f"          read by: {r.readers[0].file}")
    blockers = [r for r in results if not r.safe_to_purge]
    print(f"\n{len(results) - len(blockers)}/{len(results)} targets are regenerable "
          f"from tracked code. {len(blockers)} need review before removal.")
    return 1 if blockers else 0


def cmd_git_audit(args) -> int:
    repo = Path(args.repo or ".")
    prof = load_profile(args.profile, project_root=repo)
    try:
        a = gitaudit.audit_repo(repo, prof, include_history=not args.no_history,
                                progress=args.progress)
    except gitaudit.NotAGitRepo as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    r = a.remote
    print(f"repo      {a.repo}")
    print(f"branch    {r.branch}" + (f" -> {r.upstream}" if r.upstream else " (no upstream)"))
    if r.has_remote:
        for name, url in r.remotes.items():
            print(f"remote    {name} {url}")
        print(f"push      {'fully pushed' if r.fully_pushed else f'{r.ahead} local commit(s) unpushed'}")
    else:
        print("remote    none — history is local only")
    if a.bare:
        print("tracked   n/a — bare repository (history and remotes only)")
    else:
        print(f"tracked   {a.n_tracked} files")
    if a.tracked_bulk:
        print("\nBulk data committed:")
        for rel, size in a.tracked_bulk:
            print(f"  {inventory.human(size):>10}  {rel}")
    if a.tracked_flagged:
        print("\nIdentifiers in tracked working-tree files:")
        for f in a.tracked_flagged:
            print(f"  {f.confidence:<7} {f.hits:>8,}  {f.path}")
    if a.history_hits:
        print("\nIdentifiers in git HISTORY (present in past commits):")
        for h in a.history_hits:
            print(f"  {h.confidence:<7} {h.hits:>8,}  {h.n_versions:>3} version(s)  "
                  f"first {h.first_commit}  {h.path}")
    if a.clean:
        print("\nClean: no identifiers or bulk data in tracked files or history.")
    return 1 if not a.clean else 0


def cmd_report(args) -> int:
    prof = _profile(args)
    inv = inventory.build(_paths(args), prof, min_report_mb=args.min_mb,
                          progress=args.progress)
    git = None
    repo = Path(args.repo) if args.repo else _paths(args)[0]
    if not args.no_git and gitaudit.is_git_repo(repo):
        git = gitaudit.audit_repo(repo, prof, include_history=not args.no_history,
                                  progress=args.progress)
    preps = None if args.no_paths else pathscan.scan_paths(_paths(args), prof)
    text = report.build_report(inv, prof.name, git=git, path_reports=preps,
                               title=args.title, top_n=args.top)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_notebooks(args) -> int:
    prof = load_profile(args.profile, project_root=Path(args.files[0]).parent)
    rc = 0
    for f in args.files:
        p = Path(f)
        if args.mode == "audit":
            r = dispatch(p, prof, 3)
            icon = ICON.get(r.confidence, "?")
            print(f"{icon} {r.confidence:<6} {r.hits:>8,}  {p}  [{r.note}]")
            if r.flagged:
                rc = 1
            continue
        out = Path(args.out) / p.name if args.out else None
        res = remediate.clean_notebook(p, prof, mode="all" if args.mode == "strip" else "flagged",
                                       out_path=out, dry_run=args.dry_run)
        if res.error:
            print(f"  ! {p}: {res.error}")
            rc = 2
            continue
        verb = "would clear" if args.dry_run else "cleared"
        print(f"  {p}: {verb} {res.cleared} cell(s), preserved {res.preserved} "
              f"-> {res.out_path}")
    return rc


def cmd_redact(args) -> int:
    from . import redact_files
    prof = load_profile(args.profile, project_root=Path(args.paths[0]).parent)
    tot = {"files": 0, "matches": 0, "skipped": 0, "errors": 0}
    rc = 0
    for p in redact_files.iter_targets(Path(x) for x in args.paths):
        r = redact_files.redact_file(p, prof, execute=args.execute, min_age_min=args.min_age_min)
        if r.error:
            print(f"  ! {p}: {r.error}")
            tot["errors"] += 1
            rc = 2
        elif r.skipped:
            if r.skipped != "unsupported file type":
                print(f"  - {p}: {r.skipped}")
                tot["skipped"] += 1
        elif r.matches:
            verb = "masked" if r.written else "would mask"
            extra = f", cleared outputs of {r.cleared_outputs} cell(s)" if r.cleared_outputs else ""
            print(f"  {verb} {r.matches:>6} in {r.units} {('line' if r.kind == 'jsonl' else 'unit')}(s){extra}  [{r.kind}]  {p}")
            tot["files"] += 1
            tot["matches"] += r.matches
    mode = "EXECUTED" if args.execute else "DRY RUN (nothing written; add --execute)"
    print(f"\n{mode}: {tot['files']} file(s) with {tot['matches']:,} match(es); "
          f"{tot['skipped']} skipped as recent; {tot['errors']} error(s)")
    return rc


def cmd_purge(args) -> int:
    if args.action == "plan":
        prof = _profile(args)
        inv = inventory.build(_paths(args), prof, min_report_mb=args.min_mb,
                              progress=args.progress)
        man = purgemod.plan(inv, prof.name,
                            dispositions=tuple(d.upper() for d in args.disposition),
                            full_digest=args.full_digest, notes=args.notes,
                            progress=args.progress)
        out = Path(args.manifest)
        man.save(out)
        md = out.with_suffix(".md")
        md.write_text(purgemod.render_plan(man))
        print(f"manifest : {out}")
        print(f"review   : {md}")
        print(f"targets  : {len(man.entries)} files, "
              f"{inventory.human(man.total_bytes)}")
        print("\nNothing has been removed. Review the plan, then:")
        print(f"  {TOOL} purge execute --manifest {out} --mode quarantine "
              f"--quarantine DIR --execute")
        return 0

    if args.action == "execute":
        man = purgemod.Manifest.load(Path(args.manifest))
        dry = not args.execute
        res = purgemod.execute(
            man, mode=args.mode,
            quarantine_dir=Path(args.quarantine) if args.quarantine else None,
            log_path=Path(args.log), strict_mtime=not args.ignore_mtime, dry_run=dry)
        label = "would remove" if dry else f"{args.mode}d"
        print(f"{label}: {len(res.removed)} files, "
              f"{inventory.human(res.bytes_removed)}")
        for p, why in res.skipped:
            print(f"  SKIP  {p}  — {why}")
        for p, why in res.failed:
            print(f"  FAIL  {p}  — {why}")
        if dry:
            print(f"\nDry run. Add --execute to act. Log would be written to {args.log}")
        else:
            print(f"\nLogged to {args.log}")
            print(f"Certificate: {TOOL} purge certificate --log {args.log}")
        return 1 if res.failed else 0

    if args.action == "certificate":
        print(purgemod.certificate(Path(args.log)))
        return 0
    return 2


def cmd_install_hook(args) -> int:
    repo = Path(args.repo or ".")
    exe = Path(args.datacomp or sys.argv[0]).resolve()
    try:
        dest = remediate.install_hook(repo, exe, profile=args.profile or "generic",
                                      force=args.force)
    except (FileExistsError, subprocess_error()) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"installed {dest}")
    return 0


def subprocess_error():
    import subprocess
    return subprocess.CalledProcessError


def cmd_rewrite_history(args) -> int:
    repo = Path(args.repo)
    needs_profile = args.redact_identifiers or args.normalise_cell_ids
    prof = load_profile(args.profile, project_root=repo) if needs_profile else None
    plan = remediate.plan_rewrite(repo, args.purge_path or [], args.strip_outputs or [],
                                  redact_identifiers=args.redact_identifiers,
                                  normalise_cell_ids=args.normalise_cell_ids,
                                  profile=prof)
    print(f"repo   : {repo}")
    print(f"tool   : {plan.tool}")
    if plan.paths_to_purge:
        print("remove from all history:")
        for p in plan.paths_to_purge:
            print(f"  - {p}")
    if plan.paths_to_strip_outputs:
        print("blank notebook outputs in all history:")
        for p in plan.paths_to_strip_outputs:
            print(f"  - {p}")
    if plan.redact_identifiers:
        print("redact identifiers in ALL text blobs across history")
    if plan.normalise_cell_ids:
        print("rewrite Jupyter cell ids that match the identifier pattern")
    if not (plan.paths_to_purge or plan.paths_to_strip_outputs
            or plan.redact_identifiers or plan.normalise_cell_ids):
        print("nothing specified; pass --purge-path and/or --strip-outputs")
        return 2
    ran, msg = remediate.run_rewrite(
        plan, execute=args.execute,
        backup_dir=Path(args.backup_dir) if args.backup_dir else None)
    print(f"\n{msg}")
    return 0 if ran or not args.execute else 1


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help=f"rule profile (available: {', '.join(available_profiles())})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_paths=True):
        if with_paths:
            sp.add_argument("paths", nargs="+", type=str)
        sp.add_argument("--no-recursive", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--show", type=int, default=3, help="examples per finding")

    s = sub.add_parser("scan", help="find participant identifiers")
    common(s)
    s.add_argument("--min-hits", type=int, default=1)
    s.add_argument("--include-low", action="store_true")
    s.add_argument("--include-clean", action="store_true")
    s.add_argument("--quiet", action="store_true", help="exit code only")
    s.add_argument("--progress", action="store_true",
                   help="live counter on stderr (safe to combine with --json)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("paths", help="find server paths and credentials in source")
    common(s)
    s.add_argument("--filenames", action="store_true", help="also flag sensitive filenames")
    s.add_argument("--progress", action="store_true", help="live counter on stderr")
    s.set_defaults(func=cmd_paths)

    s = sub.add_parser("inventory", help="classify files and propose dispositions")
    common(s)
    s.add_argument("--min-mb", type=float, default=0.0)
    s.add_argument("--list", nargs="*", help="list entries for these dispositions")
    s.add_argument("--top", type=int, default=40)
    s.add_argument("--progress", action="store_true")
    s.add_argument("--include-vendor", action="store_true",
                   help="classify installed dependency trees too (default: skip)")
    s.set_defaults(func=cmd_inventory)

    s = sub.add_parser("provenance", help="can PURGE targets be regenerated?")
    common(s)
    s.add_argument("--disposition", nargs="+", default=["PURGE"])
    s.add_argument("--min-mb", type=float, default=0.0)
    s.add_argument("--code", nargs="*", help="roots to search for generating code "
                                             "(default: the scanned paths)")
    s.add_argument("--repo", help="git repo for tracked/untracked status")
    s.set_defaults(func=cmd_provenance)

    s = sub.add_parser("git-audit", help="tracked and historical exposure")
    s.add_argument("repo", nargs="?")
    s.add_argument("--no-history", action="store_true")
    s.add_argument("--progress", action="store_true")
    s.set_defaults(func=cmd_git_audit)

    s = sub.add_parser("report", help="generate a full audit document")
    common(s)
    s.add_argument("--out", "-o")
    s.add_argument("--repo", help="git repo to audit (default: first path)")
    s.add_argument("--title", default="Data Compliance Audit")
    s.add_argument("--min-mb", type=float, default=0.0)
    s.add_argument("--top", type=int, default=40)
    s.add_argument("--no-git", action="store_true")
    s.add_argument("--no-history", action="store_true")
    s.add_argument("--no-paths", action="store_true")
    s.add_argument("--progress", action="store_true")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("notebooks", help="audit or clear notebook outputs")
    s.add_argument("mode", choices=["audit", "clean", "strip"])
    s.add_argument("files", nargs="+")
    s.add_argument("--out", help="write to this directory instead of in place")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_notebooks)

    s = sub.add_parser("redact", help="mask identifiers in place (transcripts, notebooks, histories)")
    s.add_argument("paths", nargs="+")
    s.add_argument("--execute", action="store_true", help="write changes (default: dry run)")
    s.add_argument("--min-age-min", type=float, default=10.0,
                   help="refuse files modified more recently than this (minutes); live transcripts")
    s.set_defaults(func=cmd_redact)

    s = sub.add_parser("purge", help="plan and execute removal of data")
    psub = s.add_subparsers(dest="action", required=True)

    pp = psub.add_parser("plan", help="write a manifest; changes nothing")
    pp.add_argument("paths", nargs="+")
    pp.add_argument("--manifest", "-m", required=True)
    pp.add_argument("--disposition", nargs="+", default=["PURGE"])
    pp.add_argument("--min-mb", type=float, default=0.0)
    pp.add_argument("--full-digest", action="store_true",
                    help="hash entire files (slow; for evidential manifests)")
    pp.add_argument("--notes", default="")
    pp.add_argument("--progress", action="store_true")
    pp.add_argument("--json", action="store_true")
    pp.add_argument("--show", type=int, default=3)
    pp.add_argument("--no-recursive", action="store_true")
    pp.set_defaults(func=cmd_purge)

    pe = psub.add_parser("execute", help="act on a reviewed manifest")
    pe.add_argument("--manifest", "-m", required=True)
    pe.add_argument("--mode", choices=["quarantine", "delete"], default="quarantine")
    pe.add_argument("--quarantine", help="quarantine directory (required for quarantine mode)")
    pe.add_argument("--log", default="data-compliance-purge.jsonl")
    pe.add_argument("--execute", action="store_true", help="actually act (default: dry run)")
    pe.add_argument("--ignore-mtime", action="store_true",
                    help="accept mtime drift if size and digest still match")
    pe.set_defaults(func=cmd_purge)

    pc = psub.add_parser("certificate", help="render a destruction record from the log")
    pc.add_argument("--log", default="data-compliance-purge.jsonl")
    pc.set_defaults(func=cmd_purge)

    s = sub.add_parser("install-hook", help="install a pre-commit identifier guard")
    s.add_argument("repo", nargs="?")
    s.add_argument("--datacomp", help="path to this executable for the hook")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_install_hook)

    s = sub.add_parser("rewrite-history", help="guarded git-filter-repo wrapper")
    s.add_argument("repo")
    s.add_argument("--purge-path", nargs="*", help="remove these paths from all history")
    s.add_argument("--strip-outputs", nargs="*", help="blank notebook outputs in all history")
    s.add_argument("--normalise-cell-ids", action="store_true",
                   help="rewrite Jupyter cell ids containing digit runs that look "
                        "like identifiers (arbitrary metadata; a common source of "
                        "false positives in notebook repos)")
    s.add_argument("--redact-identifiers", action="store_true",
                   help="replace identifiers with a placeholder in every text blob "
                        "(catches identifiers hardcoded in source, which output "
                        "blanking leaves behind)")
    s.add_argument("--backup-dir", help="where to mirror-clone before rewriting")
    s.add_argument("--execute", action="store_true")
    s.set_defaults(func=cmd_rewrite_history)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProfileError as exc:
        print(f"profile error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
