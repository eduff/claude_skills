"""Tests for the parts where a bug is dangerous rather than merely annoying:
false negatives in detection, false positives in provenance, and the purge
safety gates.

Run with:  python3 -m pytest tests/ -q      (or: python3 tests/test_datacompliance.py)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datacompliance import inventory, provenance, purge, scan  # noqa: E402
from datacompliance.profile import load_profile  # noqa: E402

PROF = load_profile("ukb", project_root=False)


class TestIdentifierDetection(unittest.TestCase):
    def test_detects_bare_id(self):
        res = scan.scan_text("eid: 1234567", PROF)
        self.assertEqual(res.total, 1)
        self.assertEqual(res.raw, 1)
        self.assertEqual(res.supported, 1, "context word 'eid' should support the hit")

    def test_rejects_out_of_range(self):
        self.assertEqual(scan.scan_text("value 999999 and 12345678", PROF).total, 0)

    def test_ceiling_is_6500000_not_9999999(self):
        """Real eids stop at ~6.5M; a wider ceiling only adds false positives."""
        self.assertEqual(scan.scan_text("eid 6500000", PROF).total, 1)
        self.assertEqual(scan.scan_text("eid 6500001", PROF).total, 0)
        self.assertEqual(scan.scan_text("eid 9123456", PROF).total, 0)

    def test_snp_rsids_are_not_identifiers(self):
        """Genetics code is full of rsIDs; matching them would drown the signal."""
        for snp in ("rs1234567", "RS1234567", "Rs1234567", "rS1234567"):
            self.assertEqual(scan.scan_text(snp, PROF).total, 0, snp)

    def test_matches_identifiers_adjacent_to_letters_and_underscores(self):
        """\b anchors break on letter-digit and digit-underscore boundaries, so
        these real UKB forms were previously missed entirely."""
        self.assertEqual(scan.scan_text("patient5082392", PROF).raw, 1)
        self.assertEqual(scan.scan_text("3081375_20215_0_0.zip", PROF).raw, 1)

    def test_matches_ukb_official_pattern_exactly(self):
        """The UKB Git Audit Tool is the authoritative check. Diverging from its
        pattern means reporting clean where UKB reports a hit."""
        import re as _re
        official = _re.compile(
            r"(?<!\d)(?<!\.)(?<!rs)(?<!RS)(?<!Rs)(?<!rS)"
            r"(10[0-9]{5}|[1-5][0-9]{6}|6[0-4][0-9]{5}|6500000)(?!\d)")
        for case in ("1234567", "patient5082392", "3081375_20215_0_0.zip", "rs1234567",
                     "0.1234567", "6500000", "6500001", "9123456", "0123456", "123456"):
            self.assertEqual(bool(official.search(case)),
                             bool(PROF.id_regex.search(case)), case)

    def test_context_filter_never_hides_a_match(self):
        """Filtering may lower confidence; the raw count must still show it."""
        res = scan.scan_text("n_events = 1234567", PROF)
        self.assertEqual(res.total, 0, "statistics context suppresses the hit")
        self.assertEqual(res.raw, 1, "but the raw match must remain visible")

    def test_rejects_decimal_tail(self):
        self.assertEqual(scan.scan_text("HR was 1234567.85 units", PROF).total, 0)

    def test_dot_zero_needs_supporting_context(self):
        # A results table full of floats must not be flagged...
        self.assertEqual(scan.scan_text("0.5, 3805468.0, 0.99", PROF).total, 0)
        # ...but a pandas-rendered float ID column must be.
        self.assertEqual(scan.scan_text("eid\n3805468.0\n", PROF).total, 1)

    def test_statistics_context_suppresses(self):
        self.assertEqual(scan.scan_text("n_events = 1234567", PROF).total, 0)

    def test_confidence_escalates_with_density(self):
        self.assertEqual(scan.classify(0, 0, "text"), "NONE")
        self.assertEqual(scan.classify(1, 0, "text"), "LOW")
        self.assertEqual(scan.classify(10, 0, "text"), "MEDIUM")
        self.assertEqual(scan.classify(60, 0, "text"), "HIGH")
        self.assertEqual(scan.classify(1, 0, "csv"), "HIGH",
                         "column-mode hits are unambiguous")

    def test_notebook_scans_source_as_well_as_outputs(self):
        """Hardcoded identifiers in source are a real exposure that clearing
        outputs does not fix. Found in the wild as `a='1234568'` and
        `data.loc[[1234569], :]` — missed entirely by an outputs-only scan."""
        nb = {"cells": [
            {"cell_type": "code", "source": ["a = '1234568'\n"], "outputs": []},
            {"cell_type": "code", "source": ["df"],
             "outputs": [{"text": ["eid\n5654321\n"]}]},
        ]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.ipynb"
            p.write_text(json.dumps(nb))
            r = scan.scan_notebook(p, PROF)
        self.assertEqual(r.hits, 2, "one hardcoded in source, one in output")
        self.assertIn("HARDCODED IN SOURCE", r.note)
        self.assertEqual(r.confidence, "HIGH",
                         "a typed identifier is unambiguous")

    def test_notebook_source_only_still_flags_high(self):
        nb = {"cells": [{"cell_type": "code",
                         "source": ["data.loc[[1234569], :]\n"], "outputs": []}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.ipynb"
            p.write_text(json.dumps(nb))
            r = scan.scan_notebook(p, PROF)
        self.assertEqual(r.confidence, "HIGH")
        self.assertIn("clearing outputs will NOT remove these", r.note)


class TestFilenameIdentifiers(unittest.TestCase):
    """UKB bulk downloads are named <eid>_<field>_<instance>_<array>.<ext>, so
    the filename leaks even when the content is binary or never opened."""

    def test_identifier_in_filename_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "3081375_20215_0_0.txt"
            f.write_text("nothing sensitive inside\n")
            r = scan.dispatch(f, PROF)
            self.assertEqual(r.confidence, "HIGH")
            self.assertIn("FILENAME", r.note)

    def test_filename_flagged_even_when_content_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "3081375_20215_0_0.png"   # skipped by extension
            f.write_bytes(b"\x89PNG binary")
            r = scan.dispatch(f, PROF)
            self.assertEqual(r.confidence, "HIGH")
            self.assertIn("FILENAME", r.note)

    def test_ordinary_filename_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "results_2024.txt"
            f.write_text("clean\n")
            self.assertEqual(scan.dispatch(f, PROF).confidence, "NONE")


class TestClassification(unittest.TestCase):
    def _classify(self, name, size_mb=0.001):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * int(size_mb * 1024 * 1024))
            inv = inventory.build([Path(d)], PROF)
            return inv.entries[0]

    def test_backup_extension_still_classified(self):
        e = self._classify("df_core.pkl.bak")
        self.assertEqual(e.disposition, "PURGE",
                         "a .bak copy carries the same exposure as the original")

    def test_code_is_kept(self):
        self.assertEqual(self._classify("run_analysis.py").disposition, "KEEP")

    def test_notebook_needs_review(self):
        self.assertEqual(self._classify("explore.ipynb").disposition, "REVIEW")

    def test_nothing_falls_through_to_keep(self):
        e = self._classify("mystery.xyz")
        self.assertEqual(e.disposition, "REVIEW")


class TestDeduplication(unittest.TestCase):
    def test_hardlink_and_symlinked_root_counted_once(self):
        """/mnt/data -> /mnt/disk1 made every total double. Never again."""
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a"
            a.mkdir()
            f = a / "df_core.pkl"
            f.write_bytes(b"x" * 1024)
            os.link(f, a / "hardlink.pkl")
            (Path(d) / "b").symlink_to(a)
            inv = inventory.build([a, Path(d) / "b"], PROF)
            self.assertEqual(len(inv.entries), 1)
            self.assertEqual(len(inv.duplicates), 3)


class TestVendorPruning(unittest.TestCase):
    """Dependency trees are skipped by default in both the inventory and the
    source scans, but the inventory still measures their size so the byte
    accounting stays honest. --include-vendor classifies them individually."""

    def _tree(self, d):
        root = Path(d)
        vend = root / "miniconda3" / "site-packages"
        vend.mkdir(parents=True)
        (vend / "upstream.py").write_text("maintainer = 'someone@example.com'\n")
        (root / "miniconda3" / "big.pkl").write_bytes(b"x" * 4096)
        (root / "mine.py").write_text("DATA = '/mnt/store/user/data'\n")
        return root

    def test_inventory_skips_vendor_but_counts_its_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d)
            inv = inventory.build([root], PROF)
            names = {e.path.name for e in inv.entries}
            self.assertEqual(names, {"mine.py"})
            self.assertEqual(len(inv.vendor_dirs_skipped), 1)
            self.assertGreaterEqual(inv.vendor_bytes_skipped, 4096,
                                    "skipped bytes must still be measured")

    def test_include_vendor_classifies_them(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d)
            inv = inventory.build([root], PROF, skip_vendor=False)
            names = {e.path.name for e in inv.entries}
            self.assertIn("big.pkl", names)
            self.assertIn("upstream.py", names)
            self.assertEqual(inv.vendor_bytes_skipped, 0)

    def test_source_scan_always_skips_vendor(self):
        from datacompliance import paths as pathscan
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d)
            scanned = {Path(r.path).name for r in pathscan.scan_paths([root], PROF)}
            self.assertIn("mine.py", scanned)
            self.assertNotIn("upstream.py", scanned)

    def test_vendor_globs_catch_renamed_copies(self):
        """.vscode-server_old must be pruned like .vscode-server."""
        from datacompliance import paths as pathscan
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = root / ".vscode-server_old" / "ext"
            old.mkdir(parents=True)
            (old / "cfg.py").write_text("email = 'upstream@example.com'\n")
            (root / "mine.py").write_text("DATA = '/mnt/store/user/x'\n")
            scanned = {Path(r.path).name for r in pathscan.scan_paths([root], PROF)}
            self.assertNotIn("cfg.py", scanned)


class TestProvenance(unittest.TestCase):
    def _analyse(self, code, target="df_core.pkl"):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "gen.py").write_text(code)
            (root / target).write_bytes(b"x")
            res = provenance.analyse([root / target], [root], PROF,
                                     tracked={str(root / "gen.py")})
        return res[0]

    def test_real_write_is_regenerable(self):
        r = self._analyse("df.to_pickle('df_core.pkl')\n")
        self.assertEqual(r.verdict, "REGENERABLE")

    def test_commented_write_is_not_a_generator(self):
        r = self._analyse("# df.to_pickle('df_core.pkl')\n")
        self.assertEqual(r.verdict, "GENERATOR_COMMENTED_OUT")

    def test_stem_does_not_match_sibling_file(self):
        # Writing other_columns_for_review.txt must not make other_columns.pkl
        # look regenerable.
        r = self._analyse("open('other_columns_for_review.txt','w')\n",
                          target="other_columns.pkl")
        self.assertNotEqual(r.verdict, "REGENERABLE")

    def test_read_only_is_no_generator(self):
        r = self._analyse("df = pd.read_pickle('df_core.pkl')\n")
        self.assertEqual(r.verdict, "NO_GENERATOR")


class TestPurgeSafety(unittest.TestCase):
    def _manifest(self, root, path):
        inv = inventory.Inventory(roots=[root])
        st = path.stat()
        inv.entries.append(inventory.Entry(path=path, size=st.st_size,
                                           mtime=st.st_mtime, category="test",
                                           disposition="PURGE", rule="t"))
        return purge.plan(inv, "ukb")

    def test_dry_run_removes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "a" / "b" / "c"
            root.mkdir(parents=True)
            f = root / "data.pkl"
            f.write_bytes(b"payload")
            man = self._manifest(root, f)
            res = purge.execute(man, mode="delete", dry_run=True)
            self.assertTrue(f.exists(), "dry run must not delete")
            self.assertEqual(len(res.removed), 1)

    def test_changed_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "a" / "b" / "c"
            root.mkdir(parents=True)
            f = root / "data.pkl"
            f.write_bytes(b"payload")
            man = self._manifest(root, f)
            f.write_bytes(b"different payload entirely")
            res = purge.execute(man, mode="delete", dry_run=False)
            self.assertTrue(f.exists(), "modified file must not be deleted")
            self.assertEqual(len(res.removed), 0)
            self.assertEqual(len(res.skipped), 1)

    def test_quarantine_is_reversible(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "a" / "b" / "c"
            root.mkdir(parents=True)
            f = root / "data.pkl"
            f.write_bytes(b"payload")
            man = self._manifest(root, f)
            q = Path(d) / "quarantine"
            res = purge.execute(man, mode="quarantine", quarantine_dir=q,
                                log_path=Path(d) / "log.jsonl", dry_run=False)
            self.assertFalse(f.exists())
            self.assertEqual(len(res.removed), 1)
            moved = list(q.rglob("data.pkl"))
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0].read_bytes(), b"payload")

    def test_guarded_paths_refused(self):
        with self.assertRaises(purge.PurgeError):
            purge._check_target(Path("/etc"), [Path("/")])
        with self.assertRaises(purge.PurgeError):
            purge._check_target(Path.home(), [Path("/")])

    def test_target_outside_roots_refused(self):
        with self.assertRaises(purge.PurgeError):
            purge._check_target(Path("/mnt/a/b/c/x.pkl"), [Path("/mnt/other/deep")])

    def test_certificate_records_every_action(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "a" / "b" / "c"
            root.mkdir(parents=True)
            f = root / "data.pkl"
            f.write_bytes(b"payload")
            man = self._manifest(root, f)
            log = Path(d) / "log.jsonl"
            purge.execute(man, mode="delete", log_path=log, dry_run=False)
            cert = purge.certificate(log)
            self.assertIn("Data destruction record", cert)
            self.assertIn("data.pkl", cert)


class TestPreCommitHook(unittest.TestCase):
    """Verified in a throwaway repo — never against a live one."""

    def _repo(self, d):
        subprocess.run(["git", "init", "-q", d], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", d, "config", k, v], check=True)
        dc = Path(__file__).resolve().parent.parent / "bin" / "data-compliance"
        subprocess.run([sys.executable, str(dc), "--profile", "ukb", "install-hook",
                        d, "--datacomp", str(dc)], check=True,
                       stdout=subprocess.DEVNULL)
        return dc

    def _commit(self, d, name, content):
        (Path(d) / name).write_text(content)
        subprocess.run(["git", "-C", d, "add", name], check=True)
        return subprocess.run(["git", "-C", d, "commit", "-m", "x"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode

    def test_blocks_identifiers_and_allows_clean(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            # A clean commit must succeed. The hook once passed --profile after
            # the subcommand, exiting 2 and blocking *every* commit.
            self.assertEqual(self._commit(d, "notes.md", "# harmless\n"), 0,
                             "hook must not block a clean commit")
            rc = self._commit(d, "cohort.csv",
                              "eid,age\n" + "".join(f"{1000000+i},60\n" for i in range(10)))
            self.assertNotEqual(rc, 0, "hook must block staged identifiers")


class TestBareRepo(unittest.TestCase):
    """A mirror clone is bare, and a mirror clone is exactly what gets audited
    while rewriting history — so the audit must not assume a work tree."""

    def test_audit_works_on_a_bare_repo(self):
        from datacompliance import gitaudit
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            src.mkdir()
            subprocess.run(["git", "init", "-q", str(src)], check=True)
            for k, v in (("user.email", "t@t"), ("user.name", "t")):
                subprocess.run(["git", "-C", str(src), "config", k, v], check=True)
            (src / "cohort.csv").write_text(
                "eid,age\n" + "".join(f"{1000000+i},60\n" for i in range(60)))
            subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "x"], check=True)

            bare = Path(d) / "mirror.git"
            subprocess.run(["git", "clone", "-q", "--mirror", str(src), str(bare)],
                           check=True)
            self.assertTrue(gitaudit.is_bare(bare))
            a = gitaudit.audit_repo(bare, PROF)
            self.assertTrue(a.bare)
            self.assertEqual(a.n_tracked, 0, "bare repo has no work tree")
            self.assertTrue(a.history_hits, "history scan must still find identifiers")


class TestRedaction(unittest.TestCase):
    """The redaction callback rewrites history content, so its false-positive
    guards are load-bearing: a bad match corrupts code or notebook metadata
    permanently, across every commit."""

    def _run(self, data: bytes) -> bytes:
        from datacompliance.redact import build_callback
        ns = {"blob": type("B", (), {"data": data})()}
        exec(build_callback(PROF), ns)
        return ns["blob"].data

    def test_redacts_hardcoded_identifier(self):
        out = self._run(b"df[df['eid']==1234567]")
        self.assertIn(b"REDACTED_ID", out)
        self.assertNotIn(b"1234567", out)

    def test_keeps_jupyter_cell_ids(self):
        """nbformat 4.5 cell ids are UUIDs. In one real repository 137 of 183
        apparent identifiers were cell ids; redacting them corrupts the
        notebook and changes nothing about exposure."""
        for cid in (b'"id": "aa11bb22-cc33-dd44-ee55-ff6677889900"',
                    b'"id": "ab12cd34"',
                    b'"id": "bb22cc33-dd44-ee55-ff66-778899001122"'):
            self.assertEqual(self._run(cid), cid, cid)

    def test_keeps_url_fragments(self):
        """A Stack Overflow attribution link matches twice; the first run of
        this callback mangled a source credit."""
        url = b"by Fred Cirera, https://stackoverflow.com/a/1094933/1870254"
        self.assertEqual(self._run(url), url)

    def test_keeps_snps_and_out_of_range(self):
        for keep in (b"rs1234567", b"9999999", b"commit 1234567890abcdef"):
            self.assertEqual(self._run(keep), keep, keep)

    def test_redacts_identifier_adjacent_to_letters(self):
        self.assertNotIn(b"5082392", self._run(b"patient5082392"))

class TestHistoryRewrite(unittest.TestCase):
    """In git-filter-repo, `--path X` means KEEP ONLY X. Combining it with a
    blob callback would delete every unlisted file from history — so the
    output-stripping invocation must never pass --path."""

    def _capture(self, **plan_kwargs):
        from datacompliance import remediate
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            class R:
                returncode = 0
                stdout = b""
            return R()

        with tempfile.TemporaryDirectory() as d:
            plan = remediate.RewritePlan(repo=Path(d), tool="fake",
                                         paths_to_purge=plan_kwargs.get("purge", []),
                                         paths_to_strip_outputs=plan_kwargs.get("strip", []))
            orig_run, orig_backup = remediate.subprocess.run, remediate.backup_repo
            remediate.subprocess.run = fake_run
            remediate.backup_repo = lambda r, b: Path(d) / "backup.git"
            try:
                remediate.run_rewrite(plan, execute=True, backup_dir=Path(d))
            finally:
                remediate.subprocess.run = orig_run
                remediate.backup_repo = orig_backup
        return calls

    def test_strip_outputs_never_passes_path(self):
        calls = self._capture(strip=["a.ipynb", "b.ipynb"])
        self.assertEqual(len(calls), 1)
        self.assertIn("--blob-callback", calls[0])
        self.assertNotIn("--path", calls[0],
                         "--path with a blob callback deletes all other history")

    def test_purge_paths_does_pass_path_with_invert(self):
        calls = self._capture(purge=["secret.csv"])
        self.assertIn("--invert-paths", calls[0])
        self.assertIn("--path", calls[0])
        self.assertIn("secret.csv", calls[0])

    def test_refuses_without_execute_or_backup(self):
        from datacompliance import remediate
        plan = remediate.RewritePlan(repo=Path("/tmp"), tool="fake",
                                     paths_to_purge=[], paths_to_strip_outputs=["x.ipynb"])
        ran, msg = remediate.run_rewrite(plan, execute=False, backup_dir=Path("/tmp"))
        self.assertFalse(ran)
        ran, msg = remediate.run_rewrite(plan, execute=True, backup_dir=None)
        self.assertFalse(ran)
        self.assertIn("backup", msg)


class TestCLI(unittest.TestCase):
    def test_scan_exit_codes(self):
        dc = Path(__file__).resolve().parent.parent / "bin" / "data-compliance"
        with tempfile.TemporaryDirectory() as d:
            clean = Path(d) / "clean.py"
            clean.write_text("x = 1\n")
            rc = subprocess.run([sys.executable, str(dc), "scan", "--quiet", str(clean)],
                                stdout=subprocess.DEVNULL).returncode
            self.assertEqual(rc, 0)
            dirty = Path(d) / "dirty.csv"
            dirty.write_text("eid,age\n" + "".join(f"{1000000+i},50\n" for i in range(10)))
            rc = subprocess.run([sys.executable, str(dc), "scan", "--quiet", str(dirty)],
                                stdout=subprocess.DEVNULL).returncode
            self.assertEqual(rc, 1)


try:
    import numpy as _np
    import pandas as _pd
except ImportError:  # pragma: no cover
    _pd = None


@unittest.skipIf(_pd is None, "pandas not available")
class TestKeepContentCheck(unittest.TestCase):
    """A path-based KEEP (aggregate_results: */Results/*.pkl) must be verified by
    content; anything not verifiably aggregate drops to REVIEW, never further."""

    def _entry(self, name, obj=None, raw=None, text=None):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if obj is not None:
                _pd.to_pickle(obj, p)
            elif raw is not None:
                p.write_bytes(raw)
            else:
                p.write_text(text)
            return inventory.build([Path(d)], PROF).entries[0]

    def test_summary_table_stays_keep(self):
        df = _pd.DataFrame({"Risk_Factor": ["a", "b", "c"], "HR": [1.2, 0.9, 1.5],
                            "N_samples": [1076908, 1076908, 903553]})
        e = self._entry("Results/results.pkl", df)
        self.assertEqual(e.disposition, "KEEP", e.reason)
        self.assertIn("content check", e.reason)

    def test_eid_indexed_frame_drops_to_review(self):
        df = _pd.DataFrame({"x": range(100)},
                           index=_pd.Index(range(1000000, 1000100), name="eid"))
        e = self._entry("Results/per_person.pkl", df)
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "individual_derived")

    def test_unnamed_eid_range_index_detected(self):
        df = _pd.DataFrame({"score": range(50)}, index=range(2000000, 2000050))
        self.assertEqual(self._entry("Results/score.pkl", df).disposition, "REVIEW")

    def test_participant_scale_rows_without_ids_drop_to_review(self):
        # per-interval linear predictors: no identifiers, but one row per person
        df = _pd.DataFrame({"lp": _np.zeros(PROF.individual_rows + 10), "event": 0})
        e = self._entry("Results/calibration_lp.pkl", df)
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "individual_derived")

    def test_nested_per_individual_frame_detected(self):
        obj = {"summary": _pd.DataFrame({"HR": [1.1]}),
               "per_individual": _pd.DataFrame({"lp": _np.zeros(PROF.individual_rows)})}
        self.assertEqual(self._entry("Results/risk_score.pkl", obj).disposition, "REVIEW")

    def test_unreadable_pickle_is_not_kept(self):
        e = self._entry("Results/broken.pkl", raw=b"not a pickle")
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "unverified_data")

    def test_sample_sizes_are_not_identifiers(self):
        # integer columns in the id range that are NOT near-unique (sample sizes)
        df = _pd.DataFrame({"N": [1076908] * 40, "HR": _np.linspace(1, 2, 40)})
        self.assertEqual(self._entry("Results/panel.pkl", df).disposition, "KEEP")

    def test_withdrawal_list_txt_is_not_kept_as_documentation(self):
        e = self._entry("withdraw12345_20250930.txt", text="placeholder\n")
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "sensitive_filename")

    # Identifier values are computed, never written as literals, so the
    # repository's own pre-commit guard stays quiet.
    def test_id_list_txt_is_not_kept_as_documentation(self):
        ids = "\n".join(str(3 * 10**6 + 7 * i) for i in range(PROF.id_list_min + 10))
        e = self._entry("imaging/subjects.txt", text=ids + "\n")
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "individual_derived")

    def test_small_id_keyed_table_is_not_kept(self):
        rows = "\n".join(f"{3 * 10**6 + 13 * i} 2 0.{i}1 0.{i}2" for i in range(5))
        e = self._entry("imaging/IDPs_subs.txt", text="ID Visit a b\n" + rows + "\n")
        self.assertEqual(e.disposition, "REVIEW")

    def test_id_in_filename_is_not_kept(self):
        e = self._entry(f"imaging/{3 * 10**6 + 11}_25750_2_0.txt", text="0.1 0.2\n")
        self.assertEqual(e.disposition, "REVIEW")
        self.assertEqual(e.category, "id_in_filename")

    def test_prose_with_sample_sizes_stays_documentation(self):
        text = "# Notes\n\n" + "\n".join(
            f"Model {i}: N = {10**6 + 76908}, HR 1.{i}" if i % 10 == 0 else f"Paragraph {i} of text."
            for i in range(200))
        self.assertEqual(self._entry("doc/notes.md", text=text).disposition, "KEEP")

    def test_check_never_promotes_to_purge(self):
        df = _pd.DataFrame({"x": range(10)},
                           index=_pd.Index(range(1000000, 1000010), name="eid"))
        self.assertNotEqual(self._entry("Results/tiny.pkl", df).disposition, "PURGE")


class TestRedactFiles(unittest.TestCase):
    """In-place masking for archival files (redact_files)."""

    def setUp(self):
        from datacompliance import redact_files
        self.rf = redact_files
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _old(self, p):
        t = time.time() - 3600
        os.utime(p, (t, t))
        return p

    def test_jsonl_masks_text_keeps_structure(self):
        p = self.d / "t.jsonl"
        lines = [
            {"uuid": "84ea9392-20de-40cc-956f-485d1234567d", "type": "x",
             "message": {"content": "eid 1234568 1234569 printed"}},
            {"uuid": "aaa", "message": {"content": "no ids here"}},
        ]
        raw = "\n".join(json.dumps(l, separators=(",", ":")) for l in lines) + "\n"
        p.write_text(raw)
        self._old(p)
        r = self.rf.redact_file(p, PROF, execute=True, min_age_min=1)
        self.assertEqual(r.matches, 2)
        out = p.read_text().splitlines()
        first = json.loads(out[0])
        self.assertEqual(first["uuid"], lines[0]["uuid"], "structural uuid untouched")
        self.assertNotIn("1234568", first["message"]["content"])
        self.assertEqual(out[1], raw.splitlines()[1], "unmatched line byte-identical")

    def test_ipynb_masks_source_and_clears_outputs(self):
        p = self.d / "n.ipynb"
        nb = {"cells": [{"cell_type": "code", "id": "c1", "execution_count": 1,
                         "source": ["df.loc[1234568]\n"],
                         "outputs": [{"output_type": "stream", "text": ["eid 1234569\n"]}]}],
              "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        p.write_text(json.dumps(nb))
        self._old(p)
        r = self.rf.redact_file(p, PROF, execute=True, min_age_min=1)
        nb2 = json.loads(p.read_text())
        self.assertEqual(r.cleared_outputs, 1)
        self.assertNotIn("1234568", "".join(nb2["cells"][0]["source"]))
        self.assertEqual(nb2["cells"][0]["outputs"], [])

    def test_ipython_history_masked_and_vacuumed(self):
        import sqlite3
        p = self.d / "history.sqlite"
        con = sqlite3.connect(p)
        con.execute("create table history (session int, line int, source text, source_raw text)")
        con.execute("insert into history values (1,1,'df.loc[1234568]','df.loc[1234568]')")
        con.execute("insert into history values (1,2,'x = 2','x = 2')")
        con.commit(); con.close()
        self._old(p)
        r = self.rf.redact_file(p, PROF, execute=True, min_age_min=1)
        self.assertEqual(r.matches, 2)
        self.assertNotIn(b"1234568", p.read_bytes(), "old value must not survive in free pages")

    def test_recent_file_refused(self):
        p = self.d / "live.jsonl"
        p.write_text('{"message":"1234568"}\n')
        r = self.rf.redact_file(p, PROF, execute=True, min_age_min=10)
        self.assertTrue(r.skipped)
        self.assertIn("1234568", p.read_text())

    def test_dry_run_writes_nothing(self):
        p = self.d / "notes.txt"
        p.write_text("participant 1234568\n")
        self._old(p)
        before = p.read_bytes()
        r = self.rf.redact_file(p, PROF, execute=False, min_age_min=1)
        self.assertEqual(r.matches, 1)
        self.assertEqual(p.read_bytes(), before)

    def test_urls_and_snps_spared(self):
        text = "see https://stackoverflow.com/a/1094933/1870254 and rs1234568"
        new, n = self.rf.mask(text, PROF)
        self.assertEqual(n, 0)
        self.assertEqual(new, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
