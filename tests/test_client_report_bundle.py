"""Every accepted shared export carries a client PDF with optional legacy QA."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pypdf import PdfReader

from layout_configurator.cli import main
from layout_configurator.qa import validate_building_bundle


class ClientReportBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        program = cls.root / "program.json"
        program.write_text(json.dumps({"version": "0.2", "project_name": "Client evidence bundle",
            "layout": {"project_name": "Client evidence bundle", "boundary": {"width": 6000, "height": 6000},
                "rooms": [{"id": "room", "target_area": 9, "min_area": 9, "max_area": 9,
                           "min_width": 3000, "min_depth": 3000}]}, "zones": [], "equipment": [], "flows": []}))
        cls.original = cls.root / "original"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["generate-building", str(program), "--output", str(cls.original),
                         "--variants", "1", "--max-attempts", "1", "--time-limit", "2", "--seed", "1"])
        if code != 0:
            raise AssertionError(f"Actual accepted CLI fixture failed with exit {code}")

    def setUp(self):
        self.bundle = Path(tempfile.mkdtemp(dir=self.root))
        shutil.copytree(self.original, self.bundle, dirs_exist_ok=True)
        self.source = self.bundle / "building_01.json"
        self.manifest_path = self.bundle / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.entry = self.manifest["variants"][0]

    def check(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        report = validate_building_bundle(self.source)
        target = next((check for check in report.checks if check.id == "CLIENT_REPORT_READBACK"), None)
        self.assertIsNotNone(target, "Present client_report is omitted from actual bundle QA")
        return report, target

    def test_cli_shared_bundle_contains_the_exact_source_report_and_independent_readback(self):
        self.assertIn("client_report", self.entry, "Accepted shared export has no client report")
        index = self.entry["client_report"]
        pdf = self.bundle / index["pdf"]
        self.assertEqual(index["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(index["sha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())
        text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
        self.assertIn("Engineering acceptance: PENDING", text)
        report, check = self.check()
        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(check.ok, check.to_dict())

    def test_present_missing_or_malformed_report_fails_bundle_acceptance(self):
        self.assertIn("client_report", self.entry)
        original = copy.deepcopy(self.entry["client_report"])
        pdf = self.bundle / original["pdf"]
        pdf.unlink()
        report, check = self.check()
        self.assertFalse(report.ok)
        self.assertFalse(check.ok)
        shutil.copy2(self.original / pdf.name, pdf)
        for invalid in (None, {}, {**original, "sha256": "0" * 64}):
            self.entry["client_report"] = invalid
            with self.subTest(index=invalid):
                report, check = self.check()
                self.assertFalse(report.ok)
                self.assertFalse(check.ok)

    def test_legacy_bundle_without_report_index_preserves_previous_checks(self):
        self.entry.pop("client_report", None)
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        report = validate_building_bundle(self.source)
        self.assertTrue(report.ok, report.to_dict())
        self.assertNotIn("CLIENT_REPORT_READBACK", {check.id for check in report.checks})
        self.assertEqual(len(report.checks), 8)

    def test_editor_saved_revision_inherits_a_report_for_its_actual_canonical_bytes(self):
        from layout_configurator.ui import FacilityEditSession
        session = FacilityEditSession.from_input(self.source, self.bundle)
        saved = session.save_revision()
        revision = Path(saved["saved_revision"])
        entry = json.loads((revision / "manifest.json").read_text(encoding="utf-8"))["variants"][0]
        self.assertIn("client_report", entry)
        source = revision / entry["json"]
        self.assertEqual(entry["client_report"]["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        report = validate_building_bundle(source)
        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("CLIENT_REPORT_READBACK", {check.id for check in report.checks})
        reopened = FacilityEditSession.from_input(source, revision)
        files = [item["name"] for item in reopened.snapshot()["files"]]
        self.assertIn(entry["client_report"]["pdf"], files)
        self.assertIn(entry["sheet_set"]["pdf"], files)

    def test_actual_api_job_publishes_the_indexed_report_with_independent_qa(self):
        import threading
        import time
        from urllib.request import Request, urlopen
        from layout_configurator.api import create_api_server
        server = create_api_server(self.bundle / "jobs", token="client-report-test")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(route, payload=None):
            headers = {"Authorization": "Bearer client-report-test"}
            raw = None
            if payload is not None:
                raw = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            with urlopen(Request(base + route, data=raw, headers=headers), timeout=15) as response:
                return response.read()

        try:
            program = json.loads(self.source.read_text(encoding="utf-8"))["spec"]
            job = json.loads(request("/jobs", {"program": program, "variants": 1, "max_attempts": 1,
                                               "time_limit_seconds": 2}))
            deadline = time.monotonic() + 15
            while job["status"] not in {"SUCCEEDED", "FAILED"} and time.monotonic() < deadline:
                time.sleep(0.03)
                job = json.loads(request(f"/jobs/{job['id']}"))
            self.assertEqual(job["status"], "SUCCEEDED", job)
            self.assertIn("CLIENT_REPORT_READBACK", {item["id"] for item in job["qa"]["variants"][0]["checks"]})
            prefix = f"/jobs/{job['id']}/artifacts"
            self.assertIn("building_01_report.pdf", json.loads(request(prefix))["files"])
            entry = json.loads(request(prefix + "/manifest.json"))["variants"][0]
            self.assertEqual(entry["client_report"]["source"]["sha256"],
                             hashlib.sha256(request(prefix + "/building_01.json")).hexdigest())
            pdf = PdfReader(io.BytesIO(request(prefix + "/building_01_report.pdf")))
            self.assertIn("Engineering acceptance: PENDING", pdf.pages[0].extract_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_viewer_optional_inventory_requires_a_matching_source_and_local_existing_files(self):
        from layout_configurator.ui import FacilityReviewSession
        original = copy.deepcopy(self.manifest)
        for mutate in (lambda row: row["client_report"].update(pdf="../outside.pdf"),
                       lambda row: row["client_report"].update(pdf=str(self.source.resolve())),
                       lambda row: row["client_report"].update(pdf="missing.pdf"),
                       lambda row: row.update(json="another_source.json"),
                       lambda row: row.update(variant=True)):
            self.manifest = copy.deepcopy(original)
            mutate(self.manifest["variants"][0])
            self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            session = FacilityReviewSession.from_input(self.source, self.bundle)
            self.assertNotIn(original["variants"][0]["client_report"]["pdf"],
                             [item["name"] for item in session.snapshot()["files"]])
