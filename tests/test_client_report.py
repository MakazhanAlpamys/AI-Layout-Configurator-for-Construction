"""The client PDF exposes saved evidence and never invents engineering approval."""

from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.facility import (CoordinationIssue, FacilityCheckResult,
                                        FacilityValidationReport, default_facility_profile)
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.io import write_building_result
from layout_configurator.models import LayoutResult, Rect


def write_source(root, *, long=False):
    building = BuildingIR.from_mapping({"layout": {
        "project_name": "Source review \u041f\u0440\u043e\u0435\u043a\u0442",
        "boundary": {"width": 6000, "height": 3000},
        "rooms": [{"id": "process", "area": 9}, {"id": "pack", "area": 9}]},
        "flows": [{"id": "transfer", "type": "material", "from_ids": ["process"], "to_ids": ["pack"]}]})
    result = LayoutResult(1, {"process": Rect(0, 0, 3000, 3000), "pack": Rect(3000, 0, 3000, 3000)})
    routes = FlowRoutingResult((FlowRoute("transfer", "process", "pack", "process", "pack",
                                         ("process", "pack"), ((1500, 1500), (4500, 1500)), 900),))
    profile = default_facility_profile()
    profile = replace(profile, drawing=replace(profile.drawing, revision="R07"))
    rule = profile.rules[0]
    evidence = tuple(f"Measured observation {number:03d}: process and pack source IDs remain explicit."
                     for number in range(140)) if long else ("Pressure data unavailable for process",)
    checks = (
        FacilityCheckResult("GEOMETRY", "Checked geometry", "PASS", ("Two rooms, 18 square metres",)),
        FacilityCheckResult("SOURCE_RULE", "Declared process policy", "UNKNOWN", evidence,
                            rule.id, rule.source, rule.edition, rule.effective_date, rule.severity, rule.evidence),
        FacilityCheckResult("EQUIPMENT", "Equipment", "NOT_APPLICABLE", ("No equipment declared",)),
        FacilityCheckResult("SERVICE", "Service clearance", "FAIL", ("Service evidence unavailable",)),
    )
    issues = (CoordinationIssue("ISS-OPEN", "PRESSURE", "Pressure review", "Supply explicit project pressures",
                                "WARNING", "SOURCE_RULE", equipment_id=None, flow_id="transfer"),
              CoordinationIssue("ISS-CLOSED", "HISTORY", "Prior issue", "Resolved source issue",
                                "INFO", "GEOMETRY", status="RESOLVED"))
    source = root / "building_01.json"
    write_building_result(source, building, result, EquipmentLayoutResult(()), routes,
                          facility_report=FacilityValidationReport(profile, checks, issues))
    return source


class ClientReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = write_source(self.root)

    def export(self):
        try:
            module = importlib.import_module("layout_configurator.client_report")
        except ModuleNotFoundError:
            self.fail("The source-backed client PDF exporter is absent")
        return module.write_client_report(self.source)

    def text(self, index):
        return "\n".join(page.extract_text() for page in PdfReader(self.root / index["pdf"]).pages)

    def test_pdf_keeps_exact_source_identity_metrics_and_explicit_acceptance_scope(self):
        index = self.export()
        text = self.text(index)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(index["source"]["sha256"], digest)
        self.assertEqual(index["source"]["variant"], 1)
        self.assertEqual(index["source"]["revision"], "R07")
        for fragment in (digest, "Variant 1", "R07", "Engineering acceptance: PENDING",
                         "3.0 m", "0.00 %", "door crossings: 1", "\u041f\u0440\u043e\u0435\u043a\u0442",
                         "not a permit", "ventilation", "Measured geometry"):
            self.assertIn(fragment, text)
        self.assertNotIn("APPROVED", text)
        self.assertEqual(index["sha256"], hashlib.sha256((self.root / index["pdf"]).read_bytes()).hexdigest())
        reader = PdfReader(self.root / index["pdf"])
        self.assertEqual(len(reader.pages), len(index["pages"]))
        self.assertTrue(all(not page.images for page in reader.pages))

    def test_every_status_complete_rule_provenance_and_issue_details_are_printed(self):
        index = self.export()
        text = self.text(index)
        data = json.loads(self.source.read_text(encoding="utf-8"))
        for check in data["facility_validation"]["checks"]:
            self.assertIn(f"Check {check['id']} | Status: {check['status']}", text)
            for evidence in check["evidence"]:
                self.assertIn(evidence, text)
        rule = data["facility_validation"]["profile"]["rules"][0]
        for value in (rule["id"], rule["source"], rule["edition"], rule["effective_date"], *rule["evidence"]):
            self.assertIn(value, text)
        self.assertIn(json.dumps(rule["parameters"], sort_keys=True), text)
        for value in ("ISS-OPEN", "OPEN", "Supply explicit project pressures", "transfer",
                      "ISS-CLOSED", "RESOLVED", "Unknown checks: 1", "Open coordination issues: 1"):
            self.assertIn(value, text)

    def test_long_evidence_continues_without_truncation_or_duplicate_page_identity(self):
        self.source = write_source(self.root, long=True)
        index = self.export()
        self.assertGreater(len(index["pages"]), 7)
        ids = [page["page_id"] for page in index["pages"]]
        self.assertEqual(len(ids), len(set(ids)))
        text = " ".join(self.text(index).split())
        for number in range(140):
            self.assertIn(f"Measured observation {number:03d}: process and pack source IDs remain explicit.", text)
        reader = PdfReader(self.root / index["pdf"])
        for number, page in enumerate(reader.pages, 1):
            self.assertIn(f"Page {number}/{len(reader.pages)}", page.extract_text())
            self.assertIn("Engineering acceptance: PENDING", page.extract_text())
            metadata = index["pages"][number - 1]
            if metadata["kind"] == "check":
                self.assertIn(f"Check {metadata['source_id']} | Status: {metadata['status']}", page.extract_text(),
                              "A continuation page loses the check it is explaining")

    def test_invalid_or_unbounded_source_is_rejected_before_pdf_creation(self):
        original = json.loads(self.source.read_text(encoding="utf-8"))
        cases = []
        for change in ("missing", "duplicate", "bad-status", "typed-status", "typed-issue-status", "provenance", "oversize"):
            data = json.loads(json.dumps(original))
            report = data["facility_validation"]
            if change == "missing":
                del data["facility_validation"]
            elif change == "duplicate":
                report["checks"].append(report["checks"][0])
            elif change == "bad-status":
                report["checks"][0]["status"] = "APPROVED"
            elif change == "typed-status":
                report["checks"][0]["status"] = ["PASS"]
            elif change == "typed-issue-status":
                report["issues"][0]["status"] = ["OPEN"]
            elif change == "provenance":
                report["checks"][1]["rule"]["source"] = "Forged source"
            else:
                report["checks"][0]["evidence"] = ["x" * 2_000_000]
            cases.append((change, data))
        try:
            module = importlib.import_module("layout_configurator.client_report")
        except ModuleNotFoundError:
            self.fail("The source-backed client PDF exporter is absent")
        for change, data in cases:
            with self.subTest(change=change):
                self.source.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    module.write_client_report(self.source)
                self.assertFalse((self.root / "building_01_report.pdf").exists())

    def test_large_check_count_hits_page_bound_before_any_pdf_is_written(self):
        module = importlib.import_module("layout_configurator.client_report")
        data = json.loads(self.source.read_text(encoding="utf-8"))
        data["facility_validation"]["checks"] = [{"id": f"CHECK-{number}", "title": "Saved check",
                                                  "status": "PASS", "evidence": ["Finite source evidence"]}
                                                 for number in range(1000)]
        data["facility_validation"]["ok"] = True
        self.source.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            module.write_client_report(self.source)
        self.assertFalse((self.root / "building_01_report.pdf").exists())
