"""Real discovery/execution safeguards for the full CI test partition."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ci_test_shards.py"
FIXTURE_HEADER = '''import os
from pathlib import Path
import unittest
def record(value):
    with Path(os.environ['CI_SHARD_TRACE']).open('a', encoding='utf-8') as stream:
        stream.write(value + '\\n')
'''
CORE = FIXTURE_HEADER + '''class CoreCase(unittest.TestCase):
    def test_alpha(self): record('core-alpha')
    @unittest.skip('fixture skip remains visible')
    def test_skipped(self): record('must-not-run')
'''
CORE_LATER = FIXTURE_HEADER + '''class CoreLater(unittest.TestCase):
    def test_beta(self): record('core-beta')
'''
BUNDLE = FIXTURE_HEADER + '''class BundleCase(unittest.TestCase):
    def test_primary(self): record('bundle-base')
'''
FUTURE_BUNDLE = FIXTURE_HEADER + '''class FutureBundle(unittest.TestCase):
    @unittest.expectedFailure
    def test_future(self):
        record('bundle-future')
        self.fail('expected fixture failure remains an expected failure')
'''


class CITestShardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.test_root = self.folder / "tests"
        self.test_root.mkdir()
        self.trace = self.folder / "executed.txt"
        self.output = self.folder / "report.json"
        for name, contents in (("test_a_core.py", CORE), ("test_z_core.py", CORE_LATER),
                               ("test_orthogonal_bundle.py", BUNDLE),
                               ("test_orthogonal_bundle_future.py", FUTURE_BUNDLE)):
            (self.test_root / name).write_text(contents, encoding="utf-8")

    def run_runner(self, *arguments):
        self.assertTrue(RUNNER.is_file(), "CI shard runner with complete discovery safeguards is missing")
        result = subprocess.run([sys.executable, str(RUNNER), "--start-dir", str(self.test_root),
                                 "--output", str(self.output), *arguments], cwd=ROOT,
            env={**os.environ, "CI_SHARD_TRACE": str(self.trace)}, capture_output=True, text=True,
            encoding="utf-8", timeout=20)
        self.assertTrue(self.output.is_file(), result.stdout + result.stderr)
        return result, json.loads(self.output.read_text(encoding="utf-8"))

    def test_dry_partition_is_exhaustive_unique_disjoint_ordered_and_executes_no_test(self):
        result, report = self.run_runner("--verify-partition")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["discovery"]["count"], 5)
        self.assertEqual(report["discovery"]["import_errors"], [])
        self.assertEqual(report["partition"]["counts"], {"core": 3, "draft-bundles": 2})
        self.assertTrue(report["partition"]["exhaustive"])
        self.assertTrue(report["partition"]["unique"])
        self.assertTrue(report["partition"]["disjoint"])
        core, bundles = (report["partition"]["ids"][name] for name in ("core", "draft-bundles"))
        discovered = report["discovery"]["ids"]
        self.assertEqual(set(core) | set(bundles), set(discovered))
        self.assertEqual(set(core) & set(bundles), set())
        self.assertEqual(core, [identity for identity in discovered if identity in set(core)])
        self.assertEqual(bundles, [identity for identity in discovered if identity in set(bundles)])
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        summary = json.loads(result.stdout)
        self.assertEqual((summary["discovered"], summary["selected"]), (5, 0))

    def test_actual_core_and_future_bundle_execution_preserves_skips_expected_failures_and_order(self):
        for shard, expected, tests_run, skipped, expected_failures in (
                ("core", ["core-alpha", "core-beta"], 3, 1, 0),
                ("draft-bundles", ["bundle-base", "bundle-future"], 2, 0, 1)):
            with self.subTest(shard=shard):
                if self.trace.exists():
                    self.trace.unlink()
                result, report = self.run_runner("--shard", shard)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.trace.read_text(encoding="utf-8").splitlines(), expected)
                self.assertEqual(report["selection"]["ids"], report["partition"]["ids"][shard])
                self.assertEqual(report["execution"]["tests_run"], tests_run)
                self.assertEqual(report["execution"]["skipped_count"], skipped)
                self.assertEqual(report["execution"]["expected_failure_count"], expected_failures)
                self.assertEqual(report["execution"]["status"], "PASS")
                self.assertEqual(json.loads(result.stdout)["selected"], tests_run)

    def test_discovery_import_failure_stops_all_shards_before_execution(self):
        (self.test_root / "test_broken.py").write_text("raise RuntimeError('actual fixture import failure')", encoding="utf-8")
        for arguments in (("--verify-partition",), ("--shard", "core"), ("--shard", "draft-bundles")):
            with self.subTest(arguments=arguments):
                result, report = self.run_runner(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(report["discovery"]["import_errors"])
                self.assertIn("actual fixture import failure", str(report["discovery"]["import_errors"]))
                self.assertEqual(report["execution"]["status"], "NOT_RUN")
                self.assertFalse(self.trace.exists())

    def test_duplicate_imported_case_identity_stops_execution_even_when_union_would_match(self):
        (self.test_root / "test_orthogonal_bundle_alias.py").write_text("from test_a_core import CoreCase", encoding="utf-8")
        result, report = self.run_runner("--shard", "draft-bundles")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["partition"]["unique"])
        self.assertIn("test_a_core.CoreCase.test_alpha", report["partition"]["duplicate_ids"])
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertFalse(self.trace.exists())

    def test_direct_script_preserves_repository_namespace_imports_from_prior_module_runner(self):
        (self.test_root / "test_orthogonal_bundle_imports_root.py").write_text(
            "import scripts.api_smoke\n" + FIXTURE_HEADER + '''class ImportCase(unittest.TestCase):
    def test_import(self): record('repository-import')
''', encoding="utf-8")
        result, report = self.run_runner("--shard", "draft-bundles")
        self.assertEqual(result.returncode, 0, report["discovery"]["import_errors"])
        self.assertEqual(report["discovery"]["import_errors"], [])
        self.assertIn("repository-import", self.trace.read_text(encoding="utf-8").splitlines())

    def test_missing_core_or_bundle_partition_is_a_failure_before_running_any_selected_case(self):
        for missing in ("core", "draft-bundles"):
            with self.subTest(missing=missing):
                for path in self.test_root.glob("test*.py"):
                    path.unlink()
                name, source = (("test_orthogonal_bundle.py", BUNDLE) if missing == "core" else ("test_a_core.py", CORE))
                (self.test_root / name).write_text(source, encoding="utf-8")
                result, report = self.run_runner("--shard", "draft-bundles" if missing == "core" else "core")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(report["partition"]["counts"][missing], 0)
                self.assertIn(missing, " ".join(report["issues"]))
                self.assertEqual(report["execution"]["status"], "NOT_RUN")
                self.assertFalse(self.trace.exists())

    def test_actual_failures_and_unexpected_successes_keep_nonzero_exit_and_structured_evidence(self):
        for outcome in ("failure", "unexpected_success"):
            with self.subTest(outcome=outcome):
                source = FIXTURE_HEADER + ('''class CoreLater(unittest.TestCase):
    def test_beta(self):
        record('core-beta')
        self.fail('actual selected fixture failure')
''' if outcome == "failure" else '''class CoreLater(unittest.TestCase):
    @unittest.expectedFailure
    def test_beta(self): record('core-beta')
''')
                (self.test_root / "test_z_core.py").write_text(source, encoding="utf-8")
                if self.trace.exists():
                    self.trace.unlink()
                result, report = self.run_runner("--shard", "core")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["execution"]["status"], "FAIL")
                self.assertEqual(report["execution"]["failure_count"], int(outcome == "failure"))
                self.assertEqual(report["execution"]["unexpected_success_count"], int(outcome == "unexpected_success"))
                self.assertEqual(self.trace.read_text(encoding="utf-8").splitlines(), ["core-alpha", "core-beta"])

    def test_full_workflow_matrix_artifacts_and_independent_pharma_preserve_acceptance_gates(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        tests = jobs["tests"]
        self.assertEqual(tests["strategy"]["matrix"], {
            "os": ["ubuntu-latest", "windows-latest"], "shard": ["core", "draft-bundles"]})
        self.assertEqual(tests["name"], "tests (${{ matrix.os }}, ${{ matrix.shard }})")
        self.assertIs(tests["strategy"]["fail-fast"], False)
        self.assertEqual(tests["timeout-minutes"], 15)
        run = next(step["run"] for step in tests["steps"] if "ci_test_shards.py" in step.get("run", ""))
        self.assertIn("--shard ${{ matrix.shard }}", run)
        artifact = next(step for step in tests["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(artifact["if"], "always()")
        self.assertEqual(artifact["with"]["name"], "test-shard-${{ matrix.os }}-${{ matrix.shard }}")
        self.assertEqual(artifact["with"]["retention-days"], 14)
        self.assertEqual(artifact["with"]["path"], "out/ci-test-shards/*.json")
        self.assertNotIn("needs", jobs["pharma-cleanroom"])
        pharma = " ".join(step.get("run", "") for step in jobs["pharma-cleanroom"]["steps"])
        self.assertIn("--variants 3 --seeds 1 7 42 --max-attempts 9 --time-limit 30", pharma)
        self.assertIn("scripts/api_smoke.py", " ".join(step.get("run", "") for step in jobs["api-container"]["steps"]))
        quick = " ".join(step.get("run", "") for step in jobs["quick-contracts"]["steps"])
        self.assertIn("--collect-only", quick)
        self.assertIn("--verify-partition", quick)
        self.assertNotIn("--shard", quick)
        self.assertIn("tests/test_schema.py tests/test_building.py", quick)


if __name__ == "__main__":
    unittest.main()
