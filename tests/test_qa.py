import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.bcf import BcfPackageSummary
from layout_configurator.cli import main
from layout_configurator.ifc import IfcReadbackSummary
from layout_configurator.qa import (
    BundleQACheck,
    BundleQAReport,
    BuildingSetQAReport,
    validate_building_set,
)


class BuildingSetQATests(unittest.TestCase):
    def test_acceptance_set_checks_full_bundles_and_shared_exchange_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for variant in (1, 2):
                (root / f"building_{variant:02d}.json").write_text(
                    json.dumps(_payload()), encoding="utf-8"
                )

            def fake_bundle(path, *, profile=None):
                return BundleQAReport(
                    Path(path),
                    (BundleQACheck("FAKE_BUNDLE", True, "ok"),),
                )

            bcf = BcfPackageSummary(root / "building_01.bcf", "2.1", ())
            ifc = IfcReadbackSummary(
                root / "building_01.ifc",
                "stable-project",
                1,
                0,
                0,
                0,
                1,
                1,
                ("machine",),
                ("material",),
            )
            with (
                patch("layout_configurator.qa.validate_building_bundle", side_effect=fake_bundle),
                patch("layout_configurator.qa.read_bcf_package", return_value=bcf),
                patch("layout_configurator.qa.read_ifc_summary", return_value=ifc),
            ):
                report = validate_building_set(root, expected_variants=2)

            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual(len(report.variant_reports), 2)
            self.assertEqual(report.consistency.id, "CROSS_VARIANT_CONSISTENCY")
            self.assertEqual(len(report.artifact_hashes), 3)

            variant_files = report.artifact_hashes[0]["files"]
            json_hash = next(item for item in variant_files if item["name"] == "building_01.json")
            self.assertEqual(
                json_hash["sha256"],
                hashlib.sha256((root / "building_01.json").read_bytes()).hexdigest(),
            )

    def test_acceptance_set_detects_semantic_id_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "building_01.json").write_text(json.dumps(_payload()), encoding="utf-8")
            changed = _payload()
            changed["spec"]["flows"][0]["id"] = "changed_flow"
            (root / "building_02.json").write_text(json.dumps(changed), encoding="utf-8")

            def fake_bundle(path, *, profile=None):
                return BundleQAReport(
                    Path(path),
                    (BundleQACheck("FAKE_BUNDLE", True, "ok"),),
                )

            bcf = BcfPackageSummary(root / "building_01.bcf", "2.1", ())
            ifc = IfcReadbackSummary(
                root / "building_01.ifc",
                "stable-project",
                1,
                0,
                0,
                0,
                1,
                1,
                ("machine",),
                ("material",),
            )
            with (
                patch("layout_configurator.qa.validate_building_bundle", side_effect=fake_bundle),
                patch("layout_configurator.qa.read_bcf_package", return_value=bcf),
                patch("layout_configurator.qa.read_ifc_summary", return_value=ifc),
            ):
                report = validate_building_set(root, expected_variants=2)

            self.assertFalse(report.ok)
            self.assertFalse(report.consistency.ok)
            self.assertTrue(
                any(item["field"] == "semantic_ids" for item in report.consistency.details["mismatches"])
            )

    def test_cli_writes_acceptance_evidence_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "evidence" / "acceptance-report.json"
            fake_report = BuildingSetQAReport(
                root,
                (),
                BundleQACheck("CROSS_VARIANT_CONSISTENCY", True, "ok"),
                artifact_hashes=({"shared": []},),
            )
            with patch("layout_configurator.cli.validate_building_set", return_value=fake_report):
                code = main(["qa-building-set", str(root), "--report", str(report_path)])

            self.assertEqual(code, 0)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["artifact_hashes"], [{"shared": []}])


def _payload():
    return {
        "spec": {
            "layout": {"rooms": [{"id": "room"}]},
            "equipment": [{"id": "machine"}],
            "flows": [{"id": "material"}],
        },
        "flow_routes": {
            "routes": [
                {
                    "flow_id": "material",
                    "from_id": "machine",
                    "to_id": "machine",
                    "from_room_id": "room",
                    "to_room_id": "room",
                    "room_path": ["room"],
                    "minimum_clear_width_mm": 1800,
                }
            ]
        },
    }


if __name__ == "__main__":
    unittest.main()
