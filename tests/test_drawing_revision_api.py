"""Self-contained revision sources survive the real HTTP job/export path."""

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import unittest

from pypdf import PdfReader

from layout_configurator.facility import default_facility_profile
from test_drawing_revision_history import prior_record
import test_api


class DrawingRevisionApiTests(unittest.TestCase):
    setUp = test_api.FacilityApiTests.setUp
    _stop = test_api.FacilityApiTests._stop
    _request = test_api.FacilityApiTests._request
    _finish = test_api.FacilityApiTests._finish

    def payload(self):
        program = test_api._program()
        row = prior_record()
        prior = json.loads(row["source_json"])
        prior["spec"]["project_name"] = program["layout"]["project_name"]
        prior["spec"]["layout"]["project_name"] = program["layout"]["project_name"]
        row["source_json"] = json.dumps(prior, ensure_ascii=False, indent=2) + "\n \n"
        row["source_sha256"] = hashlib.sha256(row["source_json"].encode("utf-8")).hexdigest()
        profile = default_facility_profile()
        profile = replace(profile, drawing=replace(profile.drawing, revision="P02"))
        return {"program": program, "profile": profile.to_dict(), "max_attempts": 1,
                "time_limit_seconds": 2, "deterministic_units": 0.01, "reuse": True,
                "revision_history": {"current_revision": "P02", "previous_revision": "P00", "records": [row]}}

    def test_history_sources_pdf_and_cache_are_part_of_one_real_job(self):
        payload = self.payload()
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]
        job = self._finish(job_id)
        self.assertEqual(job["status"], "SUCCEEDED", job)
        filename = f"revision-sources/{payload['revision_history']['records'][0]['source_sha256']}.json"
        status, body, _ = self._request(f"/jobs/{job_id}/artifacts/{filename}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body, payload["revision_history"]["records"][0]["source_json"].encode("utf-8"))
        status, body, _ = self._request(f"/jobs/{job_id}/artifacts/building_01_sheets.pdf")
        self.assertEqual(status, 200)
        self.assertIn("Current P02 | Previous P00", PdfReader(BytesIO(body)).pages[-1].extract_text())
        self.assertIn("DRAWING_REVISION_HISTORY", {item["id"] for item in job["qa"]["variants"][0]["checks"]})
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["id"], job_id)
        payload["revision_history"]["records"][0]["author"] = "Another supplied author"
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        second = json.loads(body)["id"]
        self.assertNotEqual(second, job_id)
        self.assertEqual(self._finish(second)["status"], "SUCCEEDED")

    def test_bad_sources_or_explicit_empty_history_never_queue_work(self):
        original = self.payload()
        mutations = (
            lambda value: value.update(revision_history=None),
            lambda value: value["revision_history"].update(records=[]),
            lambda value: value["revision_history"]["records"][0].update(source_sha256="0" * 64),
            lambda value: value["revision_history"].update(current_revision="P99"),
            lambda value: value["revision_history"]["records"][0].update(source_json={}),
            lambda value: value["revision_history"]["records"][0].update(approved=True),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = deepcopy(original)
                mutation(payload)
                status, body, _ = self._request("/jobs", payload)
                self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(self._request("/jobs")[1])["jobs"], [])
        self.assertEqual(list(self.root.glob("*/request.json")), [])

    def test_invalid_history_keeps_batch_submission_atomic(self):
        valid = self.payload()
        invalid = deepcopy(valid)
        invalid["revision_history"]["previous_revision"] = "missing"
        status, body, _ = self._request("/jobs/batch", {"jobs": [valid, invalid]})
        self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(self._request("/jobs")[1])["jobs"], [])


if __name__ == "__main__":
    unittest.main()
