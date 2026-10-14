"""Real discovery/execution safeguards for the full CI test partition."""

import json
from itertools import combinations
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ci_test_shards.py"
EXPECTED_SHARDS = ("core", "draft-bundles", "exchange-and-cli", "process-packages")
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
EXCHANGE_SOURCES = {
    "test_orthogonal_cli.py": FIXTURE_HEADER + '''class CLI(unittest.TestCase):
    def test_cli(self): record('cli')
''',
    "test_facility_floors.py": FIXTURE_HEADER + '''class Floors(unittest.TestCase):
    def test_floors(self): record('floors')
''',
    "test_packaging.py": FIXTURE_HEADER + '''class Packaging(unittest.TestCase):
    def test_packaging(self): record('packaging')
''',
    "test_domain_pilots.py": FIXTURE_HEADER + '''class Domain(unittest.TestCase):
    def test_domain(self): record('domain-pilots')
''',
    "test_plan_import_cli.py": FIXTURE_HEADER + '''class Import(unittest.TestCase):
    def test_import(self): record('plan-import')
''',
    "test_orthogonal_cli_future.py": FIXTURE_HEADER + '''class FutureCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        record('exchange-setup')
        cls.cached = ['actual shared class fixture']
    @classmethod
    def tearDownClass(cls): record('exchange-teardown')
    def test_alpha(self):
        self.assertEqual(self.cached, ['actual shared class fixture'])
        record('future-alpha')
    def test_beta(self):
        self.assertEqual(self.cached, ['actual shared class fixture'])
        record('future-beta')
''',
}
EXCHANGE_TRACE = ['domain-pilots', 'floors', 'cli', 'exchange-setup', 'future-alpha',
                  'future-beta', 'exchange-teardown', 'packaging', 'plan-import']
PROCESS_PACKAGE_SOURCES = {
    "test_process_facility_package.py": FIXTURE_HEADER + '''class PackageCase(unittest.TestCase):
    def test_primary(self): record('process-package')
''',
    "test_process_facility_package_future.py": FIXTURE_HEADER + '''class FuturePackageCase(unittest.TestCase):
    def test_future(self): record('process-package-future')
''',
    "test_process_installation_facility_package.py": FIXTURE_HEADER + '''class InstallationPackageCase(unittest.TestCase):
    def test_primary(self): record('installation-package')
''',
    "test_process_installation_facility_package_future.py": FIXTURE_HEADER + '''class FutureInstallationPackageCase(unittest.TestCase):
    def test_future(self): record('installation-package-future')
''',
    "test_process_production_layout_facility_package.py": FIXTURE_HEADER + '''class ProductionPackageCase(unittest.TestCase):
    def test_primary(self): record('production-package')
''',
    "test_process_production_layout_facility_package_future.py": FIXTURE_HEADER + '''class FutureProductionPackageCase(unittest.TestCase):
    def test_future(self): record('production-package-future')
''',
}
PROCESS_PACKAGE_TRACE = ["process-package", "process-package-future", "installation-package",
                         "installation-package-future", "production-package", "production-package-future"]
CORE_PROCESS_MODULES = {
    "test_process_documentation.py": "process-documentation",
    "test_process_review.py": "process-review",
    "test_process_installation_documentation.py": "installation-documentation",
    "test_process_installation_review.py": "installation-review",
    "test_process_production_layout_documentation.py": "production-documentation",
    "test_process_production_layout_review.py": "production-review",
    "test_process_future_facility_package.py": "unowned-future-package",
    "test_process_other_package_future.py": "other-future-package",
}


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
                               ("test_orthogonal_bundle_future.py", FUTURE_BUNDLE),
                               *EXCHANGE_SOURCES.items(), *PROCESS_PACKAGE_SOURCES.items()):
            (self.test_root / name).write_text(contents, encoding="utf-8")

    def run_runner(self, *arguments):
        self.assertTrue(RUNNER.is_file(), "CI shard runner with complete discovery safeguards is missing")
        if self.output.exists():
            self.output.unlink()
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
        self.assertEqual(report["schema"], "ci-test-shards/0.2")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["discovery"]["count"], 18)
        self.assertEqual(report["discovery"]["import_errors"], [])
        self.assertEqual(list(report["partition"]["counts"]), list(EXPECTED_SHARDS))
        self.assertEqual(report["partition"]["counts"], {
            "core": 3, "draft-bundles": 2, "exchange-and-cli": 7, "process-packages": 6})
        self.assertTrue(report["partition"]["exhaustive"])
        self.assertTrue(report["partition"]["unique"])
        self.assertTrue(report["partition"]["disjoint"])
        identities = report["partition"]["ids"]
        self.assertEqual(list(identities), list(EXPECTED_SHARDS))
        discovered = report["discovery"]["ids"]
        self.assertEqual(set().union(*(set(identities[name]) for name in EXPECTED_SHARDS)), set(discovered))
        self.assertEqual(sum(len(identities[name]) for name in EXPECTED_SHARDS), len(discovered))
        for first, second in combinations(EXPECTED_SHARDS, 2):
            self.assertEqual(set(identities[first]) & set(identities[second]), set())
        for shard in EXPECTED_SHARDS:
            self.assertEqual(identities[shard], [identity for identity in discovered if identity in set(identities[shard])])
        self.assertEqual({identity.split(".", 1)[0] for identity in identities["process-packages"]},
                         {Path(name).stem for name in PROCESS_PACKAGE_SOURCES})
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        summary = json.loads(result.stdout)
        self.assertEqual((summary["discovered"], summary["selected"]), (18, 0))

    def test_actual_four_shard_execution_preserves_skips_expected_failures_and_order(self):
        for shard, expected, tests_run, skipped, expected_failures in (
                ("core", ["core-alpha", "core-beta"], 3, 1, 0),
                ("draft-bundles", ["bundle-base", "bundle-future"], 2, 0, 1),
                ("exchange-and-cli", EXCHANGE_TRACE, 7, 0, 0),
                ("process-packages", PROCESS_PACKAGE_TRACE, 6, 0, 0)):
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

    def test_process_documentation_reviews_and_unowned_future_modules_remain_in_core(self):
        for filename, value in CORE_PROCESS_MODULES.items():
            source = FIXTURE_HEADER + "class FallbackCase(unittest.TestCase):\n    def test_core(self): record(" + repr(value) + ")\n"
            (self.test_root / filename).write_text(source, encoding="utf-8")
        result, report = self.run_runner("--shard", "core")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        core_ids = set(report["partition"]["ids"]["core"])
        fallback_ids = {Path(filename).stem + ".FallbackCase.test_core" for filename in CORE_PROCESS_MODULES}
        self.assertTrue(fallback_ids <= core_ids)
        for shard in EXPECTED_SHARDS[1:]:
            self.assertFalse(fallback_ids & set(report["partition"]["ids"][shard]))
        self.assertEqual(report["partition"]["counts"], {
            "core": 3 + len(CORE_PROCESS_MODULES), "draft-bundles": 2,
            "exchange-and-cli": 7, "process-packages": 6})
        self.assertEqual(self.trace.read_text(encoding="utf-8").splitlines(),
                         ["core-alpha", *(CORE_PROCESS_MODULES[name] for name in sorted(CORE_PROCESS_MODULES)), "core-beta"])
        self.assertEqual(report["execution"]["tests_run"], 3 + len(CORE_PROCESS_MODULES))

    def test_discovery_import_failure_stops_all_shards_before_execution(self):
        for filename in ("test_broken.py", "test_process_production_layout_facility_package_broken.py"):
            broken = self.test_root / filename
            broken.write_text("raise RuntimeError('actual fixture import failure')", encoding="utf-8")
            try:
                for arguments in (("--verify-partition",), *(("--shard", shard) for shard in EXPECTED_SHARDS)):
                    with self.subTest(filename=filename, arguments=arguments):
                        result, report = self.run_runner(*arguments)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(report["status"], "FAIL")
                        self.assertTrue(report["discovery"]["import_errors"])
                        self.assertIn("actual fixture import failure", str(report["discovery"]["import_errors"]))
                        self.assertEqual(report["execution"]["status"], "NOT_RUN")
                        self.assertFalse(self.trace.exists())
            finally:
                broken.unlink()

    def test_duplicate_imported_case_identity_stops_execution_even_when_union_would_match(self):
        (self.test_root / "test_orthogonal_bundle_alias.py").write_text("from test_a_core import CoreCase", encoding="utf-8")
        result, report = self.run_runner("--shard", "draft-bundles")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["partition"]["unique"])
        self.assertIn("test_a_core.CoreCase.test_alpha", report["partition"]["duplicate_ids"])
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertFalse(self.trace.exists())

    def test_collision_between_two_noncore_shards_is_not_disjoint_and_stops_everything(self):
        (self.test_root / "test_orthogonal_cli_collision.py").write_text(FIXTURE_HEADER + '''class Collision(unittest.TestCase):
    def id(self): return 'test_orthogonal_bundle.BundleCase.test_primary'
    def test_collision(self): record('must-not-run')
''', encoding="utf-8")
        result, report = self.run_runner("--shard", "core")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report["partition"]["unique"])
        self.assertFalse(report["partition"]["disjoint"])
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertFalse(self.trace.exists())

    def test_process_package_identity_collision_with_core_stops_all_execution(self):
        (self.test_root / "test_process_facility_package_collision.py").write_text(FIXTURE_HEADER + '''class Collision(unittest.TestCase):
    def id(self): return 'test_a_core.CoreCase.test_alpha'
    def test_collision(self): record('must-not-run')
''', encoding="utf-8")
        result, report = self.run_runner("--shard", "process-packages")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["partition"]["unique"])
        self.assertFalse(report["partition"]["disjoint"])
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

    def test_any_missing_partition_is_a_failure_before_running_any_selected_case(self):
        names = {"core": ("test_a_core.py", "test_z_core.py"),
                 "draft-bundles": ("test_orthogonal_bundle.py", "test_orthogonal_bundle_future.py"),
                 "exchange-and-cli": tuple(EXCHANGE_SOURCES),
                 "process-packages": tuple(PROCESS_PACKAGE_SOURCES)}
        for missing in names:
            with self.subTest(missing=missing):
                if self.trace.exists():
                    self.trace.unlink()
                removed = {name: (self.test_root / name).read_text(encoding="utf-8") for name in names[missing]}
                for name in removed:
                    (self.test_root / name).unlink()
                try:
                    result, report = self.run_runner("--shard", "draft-bundles" if missing == "core" else "core")
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(missing, report["partition"]["counts"])
                    self.assertEqual(report["partition"]["counts"][missing], 0)
                    self.assertIn(missing, " ".join(report["issues"]))
                    self.assertEqual(report["execution"]["status"], "NOT_RUN")
                    self.assertFalse(self.trace.exists())
                finally:
                    for name, source in removed.items():
                        (self.test_root / name).write_text(source, encoding="utf-8")

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

    def test_exchange_failures_errors_and_failed_subtests_are_retained_without_failfast(self):
        for outcome, body in (("failure", "self.fail('actual exchange failure')"),
                              ("error", "raise RuntimeError('actual exchange error')"),
                              ("subtest", "for value in (True, False):\n            with self.subTest(value=value):\n                self.assertTrue(value)")):
            with self.subTest(outcome=outcome):
                (self.test_root / "test_packaging.py").write_text(FIXTURE_HEADER +
                    "class Packaging(unittest.TestCase):\n    def test_packaging(self):\n        record('packaging')\n        " +
                    body + "\n", encoding="utf-8")
                if self.trace.exists():
                    self.trace.unlink()
                result, report = self.run_runner("--shard", "exchange-and-cli")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report["execution"]["tests_run"], 7)
                self.assertEqual(report["execution"]["error_count"], int(outcome == "error"))
                self.assertEqual(report["execution"]["failure_count"], int(outcome != "error"))
                self.assertEqual(report["execution"]["subtests"], 2 if outcome == "subtest" else 0)
                self.assertEqual(self.trace.read_text(encoding="utf-8").splitlines(), EXCHANGE_TRACE)

    def test_process_package_failures_errors_and_subtests_are_local_and_retained_without_failfast(self):
        for outcome, body in (("failure", "self.fail('actual process package failure')"),
                              ("error", "raise RuntimeError('actual process package error')"),
                              ("subtest", "for value in (True, False):\n            with self.subTest(value=value):\n                self.assertTrue(value)")):
            with self.subTest(outcome=outcome):
                (self.test_root / "test_process_facility_package.py").write_text(FIXTURE_HEADER +
                    "class PackageCase(unittest.TestCase):\n    def test_primary(self):\n        record('process-package')\n        " +
                    body + "\n", encoding="utf-8")
                if self.trace.exists():
                    self.trace.unlink()
                result, report = self.run_runner("--shard", "process-packages")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report["execution"]["tests_run"], 6)
                self.assertEqual(report["execution"]["error_count"], int(outcome == "error"))
                self.assertEqual(report["execution"]["failure_count"], int(outcome != "error"))
                self.assertEqual(report["execution"]["subtests"], 2 if outcome == "subtest" else 0)
                self.assertEqual(self.trace.read_text(encoding="utf-8").splitlines(), PROCESS_PACKAGE_TRACE)
                evidence = report["execution"]["errors" if outcome == "error" else "failures"]
                self.assertEqual(len(evidence), 1)
                self.assertTrue(evidence[0]["test_id"].startswith("test_process_facility_package.PackageCase.test_primary"))

    def test_full_workflow_matrix_artifacts_and_independent_pharma_preserve_acceptance_gates(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        tests = jobs["tests"]
        self.assertEqual(tests["strategy"]["matrix"], {
            "os": ["ubuntu-latest", "windows-latest"], "shard": list(EXPECTED_SHARDS)})
        self.assertEqual(len(tests["strategy"]["matrix"]["os"]) * len(tests["strategy"]["matrix"]["shard"]), 8)
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
        self.assertEqual(jobs["quick-contracts"]["if"], "github.event_name == 'push'")
        for name in ("tests", "api-container", "pharma-cleanroom"):
            self.assertEqual(jobs[name]["if"], "github.event_name != 'push'")
        quick = " ".join(step.get("run", "") for step in jobs["quick-contracts"]["steps"])
        self.assertIn("--collect-only", quick)
        self.assertIn("--verify-partition", quick)
        self.assertNotIn("--shard", quick)
        contract_files = []
        for step in jobs["quick-contracts"]["steps"]:
            for line in step.get("run", "").splitlines():
                arguments = shlex.split(line)
                if arguments[:3] != ["python", "-m", "pytest"] or "--collect-only" in arguments:
                    continue
                selected = [value for value in arguments[3:] if value.startswith("tests/")]
                self.assertEqual(len(selected), 1, "Quick contract checks must execute one file per invocation")
                self.assertFalse({";", "&&", "&", "||"} & set(arguments),
                                 "Quick contract commands must retain sequential execution")
                self.assertEqual(arguments, ["python", "-m", "pytest", "-q", selected[0]])
                contract_files.extend(selected)
        self.assertEqual(contract_files, ["tests/test_schema.py", "tests/test_building.py",
                                         "tests/test_result_geometry.py", "tests/test_orthogonal.py"])


if __name__ == "__main__":
    unittest.main()
