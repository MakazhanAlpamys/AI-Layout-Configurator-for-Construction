"""Run an exhaustive, disjoint partition of the existing unittest discovery.

Every invocation discovers the complete test root once for four groups.
Draft-bundles keeps modules starting with test_orthogonal_bundle. Complete
CLI, floor exchange, packaging and domain-pilot modules form exchange-and-cli.
The three process facility-package module families form process-packages;
core keeps the remainder, including process documentation and review modules.
Future modules follow the same prefixes.
Collection failures, duplicate identities and empty partitions stop execution.
"""

from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
import platform
import sys
import time
import unittest


SHARDS = ("core", "draft-bundles", "exchange-and-cli", "process-packages")
BUNDLE_MODULE_PREFIX = "test_orthogonal_bundle"
EXCHANGE_MODULE_PREFIXES = (
    "test_orthogonal_cli", "test_facility_floors", "test_packaging",
    "test_domain_pilots", "test_plan_import_cli",
)
PROCESS_PACKAGE_MODULE_PREFIXES = (
    "test_process_facility_package", "test_process_installation_facility_package",
    "test_process_production_layout_facility_package",
)
MAX_DETAIL_RECORDS = 100
MAX_DETAIL_CHARACTERS = 2048


def flatten(suite):
    """Preserve the complete loader's relative case order without rediscovery."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise TypeError("Discovery contains an unsupported non-TestCase item")


def partition(cases):
    selected = {name: [] for name in SHARDS}
    for case in cases:
        module = type(case).__module__.rsplit(".", 1)[-1]
        if module.startswith(BUNDLE_MODULE_PREFIX):
            shard = "draft-bundles"
        elif module.startswith(EXCHANGE_MODULE_PREFIXES):
            shard = "exchange-and-cli"
        elif module.startswith(PROCESS_PACKAGE_MODULE_PREFIXES):
            shard = "process-packages"
        else:
            shard = "core"
        selected[shard].append(case)
    identities = [case.id() for case in cases]
    if any(type(identity) is not str or not identity for identity in identities):
        raise ValueError("Collected test identities must be nonempty strings")
    ids = {name: [case.id() for case in selected[name]] for name in SHARDS}
    duplicates = [identity for identity, count in Counter(identities).items() if count != 1]
    sets = {name: set(values) for name, values in ids.items()}
    evidence = {"status": "FAIL", "counts": {name: len(values) for name, values in ids.items()},
                "ids": ids, "duplicate_ids": duplicates, "unique": not duplicates,
                "disjoint": all(not (sets[a] & sets[b]) for a, b in combinations(SHARDS, 2)),
                "exhaustive": set().union(*sets.values()) == set(identities)
                    and sum(len(values) for values in ids.values()) == len(identities)}
    issues = []
    if not evidence["unique"]:
        issues.append("Discovery contains duplicated test identities")
    if not evidence["disjoint"] or not evidence["exhaustive"]:
        issues.append("Shard partition is not a disjoint exhaustive copy of discovery")
    for name in SHARDS:
        if not ids[name]:
            issues.append("Required shard is empty: " + name)
        elif ids[name] != [identity for identity in identities if identity in sets[name]]:
            issues.append("Shard does not preserve discovery order: " + name)
    if not issues:
        evidence["status"] = "PASS"
    return selected, evidence, issues


class ShardResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subtest_count = 0

    def addSubTest(self, test, subtest, error):
        self.subtest_count += 1
        super().addSubTest(test, subtest, error)


def details(entries):
    """Keep complete outcome counts with bounded diagnostic samples."""
    return [{"test_id": test.id(), "reason": reason[:MAX_DETAIL_CHARACTERS]}
            for test, reason in entries[:MAX_DETAIL_RECORDS]]


def run(start_dir: Path, shard: str | None):
    started = time.perf_counter()
    # Direct script invocation otherwise omits the repository namespace from
    # sys.path. Match python -m unittest so tests can still import scripts.*.
    repository = str(Path(__file__).resolve().parents[1])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    report = {"schema": "ci-test-shards/0.2", "status": "FAIL",
              "mode": "run" if shard is not None else "verify-partition",
              "platform": platform.system(), "start_dir": str(start_dir),
              "discovery": {"count": 0, "ids": [], "import_errors": []},
              "partition": {"status": "FAIL", "counts": {name: 0 for name in SHARDS},
                  "ids": {name: [] for name in SHARDS}, "duplicate_ids": [],
                  "unique": False, "disjoint": False, "exhaustive": False},
              "selection": {"shard": shard, "count": 0, "ids": []},
              "execution": {"status": "NOT_RUN", "tests_run": 0}, "issues": []}
    loader = unittest.TestLoader()
    try:
        # Match the existing full CI command's unittest discovery and pattern.
        suite = loader.discover(start_dir=str(start_dir))
        cases = list(flatten(suite))
        report["discovery"].update(count=len(cases), ids=[case.id() for case in cases],
            import_errors=[error[:MAX_DETAIL_CHARACTERS] for error in loader.errors])
        selected, report["partition"], issues = partition(cases)
        report["issues"].extend(issues)
        if loader.errors:
            report["partition"]["status"] = "FAIL"
            report["issues"].append("Complete unittest discovery contains import or loader errors")
        report["discovery"]["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        if report["issues"]:
            exit_code = 2
        elif shard is None:
            report["status"] = "PASS"
            exit_code = 0
        else:
            report["selection"].update(count=len(selected[shard]), ids=report["partition"]["ids"][shard])
            execution_started = time.perf_counter()
            result = unittest.TextTestRunner(verbosity=2, resultclass=ShardResult,
                                            failfast=False).run(unittest.TestSuite(selected[shard]))
            report["execution"] = {
                "status": "PASS" if result.wasSuccessful() else "FAIL", "tests_run": result.testsRun,
                "subtests": result.subtest_count, "elapsed_seconds": round(time.perf_counter() - execution_started, 6),
                "failure_count": len(result.failures), "failures": details(result.failures),
                "error_count": len(result.errors), "errors": details(result.errors),
                "skipped_count": len(result.skipped), "skipped": details(result.skipped),
                "expected_failure_count": len(result.expectedFailures), "expected_failures": details(result.expectedFailures),
                "unexpected_success_count": len(result.unexpectedSuccesses),
                "unexpected_successes": [test.id() for test in result.unexpectedSuccesses[:MAX_DETAIL_RECORDS]],
                "detail_limits": {"records_per_outcome": MAX_DETAIL_RECORDS,
                                  "reason_characters": MAX_DETAIL_CHARACTERS}}
            report["status"] = report["execution"]["status"]
            exit_code = 0 if result.wasSuccessful() else 1
    except Exception as error:
        report["issues"].append("Test discovery or shard execution failed: " + str(error)[:MAX_DETAIL_CHARACTERS])
        exit_code = 2
    report["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return report, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-partition", action="store_true", help="Verify full coverage without running any tests")
    mode.add_argument("--shard", choices=SHARDS, help="Run exactly one verified full-discovery partition")
    parser.add_argument("--start-dir", type=Path, default=Path("tests"), help="Complete unittest discovery root")
    parser.add_argument("--output", type=Path, help="Structured evidence file; defaults under ignored out/ci-test-shards")
    args = parser.parse_args(argv)
    report, exit_code = run(args.start_dir, args.shard)
    output = args.output or Path("out/ci-test-shards") / (platform.system().lower() + "-" + (args.shard or "partition") + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mode": report["mode"], "shard": args.shard,
        "discovered": report["discovery"]["count"], "selected": report["selection"]["count"],
        "partition_counts": report["partition"]["counts"],
        "import_errors": len(report["discovery"]["import_errors"]), "issues": report["issues"],
        "elapsed_seconds": report["elapsed_seconds"], "evidence": str(output)}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
