import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.building import BuildingIR


def _program():
    return BuildingIR.from_mapping({
        "version": "0.2",
        "layout": {
            "project_name": "API acceptance fixture",
            "boundary": {"width": 6000, "height": 6000},
            "rooms": [{"id": "room", "type": "work", "target_area": 9,
                       "min_area": 9, "max_area": 9,
                       "min_width": 3000, "min_depth": 3000}],
        },
    }).to_dict()


class FacilityApiTests(unittest.TestCase):
    def setUp(self):
        # The behavior tests fail explicitly while the adapter is absent; once
        # present they exercise real HTTP, CP-SAT, exports and independent QA.
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.api"),
                             "The generation/check/review HTTP API is missing")
        from layout_configurator.api import create_api_server
        self.create_server = create_api_server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = create_api_server(self.root, ("127.0.0.1", 0), token="test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, path, payload=None, *, token="test-token", method=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, response.read(), response.headers
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def _finish(self, job_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status, body, _ = self._request(f"/jobs/{job_id}")
            self.assertEqual(status, 200)
            result = json.loads(body)
            if result["status"] in ("SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"):
                return result
            time.sleep(0.03)
        self.fail("A one-room API job did not finish within the fixture budget")

    def test_generate_check_and_review_use_the_same_complete_bundle(self):
        status, body, _ = self._request("/jobs", {"program": _program(), "variants": 1,
                                                  "max_attempts": 1, "time_limit_seconds": 2})
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]
        job = self._finish(job_id)
        self.assertEqual(job["status"], "SUCCEEDED", job)
        status, body, _ = self._request(f"/jobs/{job_id}/check", {}, method="POST")
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["ok"])
        status, body, _ = self._request(f"/jobs/{job_id}/artifacts")
        self.assertEqual(status, 200)
        files = json.loads(body)["files"]
        for suffix in (".json", ".dxf", ".pdf", ".ifc", ".bcf"):
            self.assertIn(f"building_01{suffix}", files)
        status, body, headers = self._request(f"/jobs/{job_id}/artifacts/building_01.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertIn("application/pdf", headers["Content-Type"])
        status, body, _ = self._request(f"/jobs/{job_id}/review")
        self.assertEqual(status, 200)
        review = json.loads(body)
        self.assertEqual(len([name for name in review["files"] if name.endswith(".md")]), 3)
        self.assertEqual(review["expert_acceptance"], "PENDING")

    def test_invalid_program_and_unknown_request_fields_never_queue_work(self):
        for payload in ({"program": _program(), "output": "../../outside"},
                        {"program": {**_program(), "coordinates": [1, 2]}},
                        {"program": _program(), "variants": 0},
                        {"program": _program(), "time_limit_seconds": float("nan")}):
            with self.subTest(payload=payload):
                status, body, _ = self._request("/jobs", payload)
                self.assertEqual(status, 400, body)
        status, body, _ = self._request("/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["jobs"], [])

    def test_authentication_and_artifact_paths_do_not_expose_other_files(self):
        (self.root / "private.txt").write_text("private", encoding="utf-8")
        self.assertEqual(self._request("/jobs", token=None)[0], 401)
        self.assertEqual(self._request("/jobs", token="wrong")[0], 401)
        self.assertEqual(self._request("/jobs/no-such-job/artifacts/..%2Fprivate.txt")[0], 404)
        self.assertEqual(self._request("/jobs/..%2Fprivate.txt")[0], 404)

    def test_failed_job_retains_search_evidence_and_does_not_publish_artifacts(self):
        program = _program()
        program["layout"]["boundary"] = {"width": 2000, "height": 2000}
        status, body, _ = self._request("/jobs", {"program": program, "max_attempts": 1,
                                                  "time_limit_seconds": 1})
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]
        job = self._finish(job_id)
        self.assertEqual(job["status"], "FAILED", job)
        self.assertIn("generation", job["error"])
        self.assertEqual(self._request(f"/jobs/{job_id}/artifacts")[0], 409)
        self.assertTrue((self.root / job_id / "generation-failure.json").is_file())

    def test_completed_job_survives_restart(self):
        status, body, _ = self._request("/jobs", {"program": _program(), "max_attempts": 1,
                                                  "time_limit_seconds": 2})
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]
        self.assertEqual(self._finish(job_id)["status"], "SUCCEEDED")
        from layout_configurator.api import FacilityService
        other = FacilityService(self.root, start_worker=False)
        self.addCleanup(other.close)
        self.assertEqual(other.get(job_id)["status"], "SUCCEEDED")
        self.assertTrue(other.check(job_id)["ok"])

    def test_deterministic_cache_reuses_matching_program_and_invalidates_changed_input(self):
        payload = {"program": _program(), "max_attempts": 1, "time_limit_seconds": 2,
                   "deterministic_units": 0.01, "reuse": True}
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        first = json.loads(body)["id"]
        self.assertEqual(self._finish(first)["status"], "SUCCEEDED")
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["id"], first)
        self.assertTrue(json.loads(body)["cache_hit"])
        payload["program"]["layout"]["project_name"] = "Changed program"
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        self.assertNotEqual(json.loads(body)["id"], first)
        self.assertEqual(self._finish(json.loads(body)["id"])["status"], "SUCCEEDED")

    def test_batch_validation_is_atomic_and_valid_jobs_have_separate_bundles(self):
        good = {"program": _program(), "max_attempts": 1, "time_limit_seconds": 2}
        status, body, _ = self._request("/jobs/batch", {"jobs": [good, {"program": {}}]})
        self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(self._request("/jobs")[1])["jobs"], [])
        status, body, _ = self._request("/jobs/batch", {"jobs": [good, good]})
        self.assertEqual(status, 202, body)
        jobs = json.loads(body)["jobs"]
        self.assertEqual(len({job["id"] for job in jobs}), 2)
        for job in jobs:
            self.assertEqual(self._finish(job["id"])["status"], "SUCCEEDED")

    def test_nonlocal_binding_requires_an_explicit_token(self):
        with self.assertRaises(ValueError):
            self.create_server(self.root / "public", ("0.0.0.0", 0))

    def test_a_second_writer_cannot_own_the_same_job_directory(self):
        with self.assertRaisesRegex(ValueError, "already|active|writer"):
            other = self.create_server(self.root, ("127.0.0.1", 0), token="test-token")
            self.addCleanup(other.server_close)

    def test_queued_job_cancellation_persists_without_exporting_a_bundle(self):
        from layout_configurator.api import FacilityService
        service = FacilityService(self.root / "paused", start_worker=False)
        self.addCleanup(service.close)
        job = service.submit({"program": _program(), "max_attempts": 1})
        self.assertTrue(callable(getattr(service, "cancel", None)), "Queued jobs must be cancellable")
        result = service.cancel(job["id"])
        self.assertEqual(result["status"], "CANCELLED")
        restored = FacilityService(self.root / "paused", start_worker=False)
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(job["id"])["status"], "CANCELLED")
        self.assertEqual(list((self.root / "paused" / job["id"]).glob("*.ifc")), [])

    def test_modified_artifacts_are_not_downloaded_or_reused_from_cache(self):
        payload = {"program": _program(), "max_attempts": 1, "time_limit_seconds": 2,
                   "deterministic_units": 0.01, "reuse": True}
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202)
        first = json.loads(body)["id"]
        self.assertEqual(self._finish(first)["status"], "SUCCEEDED")
        (self.root / first / "building_01.pdf").write_bytes(b"modified")
        self.assertEqual(self._request(f"/jobs/{first}/artifacts/building_01.pdf")[0], 409)
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        second = json.loads(body)["id"]
        self.assertNotEqual(second, first)
        self.assertEqual(self._finish(second)["status"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
