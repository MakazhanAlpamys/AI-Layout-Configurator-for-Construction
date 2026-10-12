"""A short route cannot turn unresolved or absent facility evidence into PASS."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from layout_configurator.compare import compare_bundle, variant_metrics, write_comparison


class ComparisonGateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def source(self, variant=1, *, checks=None, ok=True, missing=False, route_mm=1000):
        data = {
            "spec": {"project_name": "Gate evidence", "layout": {
                "rooms": [{"id": "room", "target_area": 1}],
                "boundary": {"width": 1000, "height": 1000}},
                "flows": [{"id": "process", "type": "material"}]},
            "layout": {"variant": variant, "rooms": {"room": {
                "x": 0, "y": 0, "width": 1000, "height": 1000, "area_m2": 1}}},
            "flow_routes": {"routes": [{"flow_id": "process", "from_id": "room", "to_id": "room",
                "points": [{"x": 0, "y": 0}, {"x": route_mm, "y": 0}], "room_path": ["room"]}]},
        }
        if not missing:
            data["facility_validation"] = {"ok": ok, "checks": checks if checks is not None else [
                {"id": "GEOMETRY", "status": "PASS", "evidence": ["One checked room"]}], "issues": []}
        path = self.root / f"building_{variant:02d}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_unknown_evidence_is_visible_in_json_csv_and_actual_pdf_without_false_pass(self):
        self.source(checks=[{"id": "PRESSURE", "status": "UNKNOWN", "evidence": ["No declared pressure"]}], ok=False)
        written = write_comparison(self.root)
        result = json.loads(written["json"].read_text())["variants"][0]
        self.assertFalse(result["facility_ok"])
        self.assertNotIn("passes every facility gate", result["reasons"])
        self.assertEqual(result["unknown_checks"], ["PRESSURE"])
        with written["csv"].open() as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["unknown_checks"], "PRESSURE")
        text = "\n".join(page.extract_text() for page in PdfReader(written["pdf"]).pages)
        self.assertIn("UNKNOWN", text)
        self.assertIn("No accepted variant", text)
        self.assertNotIn("Recommended: variant", text)
        self.assertNotIn("passes every facility gate", text)

    def test_missing_empty_or_contradictory_evidence_cannot_establish_acceptance(self):
        for settings in ({"missing": True}, {"checks": []}, {"ok": False}, {"ok": "true"},
                         {"checks": [{"id": "PRESSURE", "status": "UNKNOWN"}], "ok": True},
                         {"checks": [{"id": "GEOMETRY", "status": "FAIL"}], "ok": True}):
            with self.subTest(settings=settings):
                path = self.source(**settings)
                self.assertFalse(variant_metrics(path).facility_ok)
                self.assertNotIn("passes every facility gate", compare_bundle(self.root)[0].reasons)

    def test_accepted_evidence_ranks_before_shorter_unknown_or_missing_evidence(self):
        self.source(1, checks=[{"id": "PRESSURE", "status": "UNKNOWN"}], ok=False, route_mm=1)
        self.source(2, route_mm=3000)
        self.source(3, missing=True, route_mm=0)
        ranked = compare_bundle(self.root)
        self.assertEqual(ranked[0].variant, 2)
        self.assertTrue(ranked[0].facility_ok)
        self.assertIn("passes every facility gate", ranked[0].reasons)

    def test_fail_and_unknown_are_both_reported_when_a_variant_has_both(self):
        self.source(checks=[{"id": "GEOMETRY", "status": "FAIL"},
                            {"id": "PRESSURE", "status": "UNKNOWN"}], ok=False)
        ranked = compare_bundle(self.root)[0]
        self.assertTrue(any("GEOMETRY" in reason for reason in ranked.reasons))
        self.assertTrue(any("PRESSURE" in reason and "UNKNOWN" in reason for reason in ranked.reasons))

    def test_not_applicable_check_is_accepted_with_actual_pass_evidence(self):
        self.source(checks=[{"id": "GEOMETRY", "status": "PASS"},
                            {"id": "EQUIPMENT", "status": "NOT_APPLICABLE"}], ok=True)
        self.assertTrue(variant_metrics(self.root / "building_01.json").facility_ok)
