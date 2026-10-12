"""Authenticated HTTP program edits reuse only source-pinned accepted jobs."""

import hashlib
import json
import unittest

from layout_configurator.api import FacilityService
from layout_configurator.facility import FacilityProfile
import test_api as api_fixture
from test_facility_incremental import building


class FacilityIncrementalApiTests(unittest.TestCase):
    setUp = api_fixture.FacilityApiTests.setUp
    _stop = api_fixture.FacilityApiTests._stop
    _request = api_fixture.FacilityApiTests._request
    _finish = api_fixture.FacilityApiTests._finish

    def payload(self, *, edited=False, prior=None):
        program = building().to_dict()
        if edited:
            next(room for room in program["layout"]["rooms"] if room["id"] == "b_leaf_0").update(
                target_area=6, min_area=6, max_area=6, min_width=3000, min_depth=2000)
        result = {"program": program, "variants": 1, "max_attempts": 1, "seed": 1,
                  "time_limit_seconds": 1, "room_solver": "hierarchical", "deterministic_units": .1}
        if prior is not None:
            result["incremental_from"] = {"job_id": prior, "variant": 1}
        return result

    def accepted_source(self):
        status, body, _ = self._request("/jobs", self.payload())
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]
        self.assertEqual(self._finish(job_id)["status"], "SUCCEEDED")
        return job_id

    def test_actual_http_edit_has_fresh_full_bundle_and_exact_parent_provenance(self):
        prior = self.accepted_source()
        parent_path = self.root / prior / "building_01.json"
        before = parent_path.read_bytes()
        status, body, _ = self._request("/jobs", self.payload(edited=True, prior=prior))
        self.assertEqual(status, 202, body)
        edited = json.loads(body)["id"]
        job = self._finish(edited)
        self.assertEqual(job["status"], "SUCCEEDED", job)
        source = json.loads((self.root / edited / "building_01.json").read_text(encoding="utf-8"))
        evidence = source["generation"]["incremental_evidence"]
        self.assertEqual(evidence["source_job_id"], prior)
        self.assertEqual(evidence["source_variant"], 1)
        self.assertEqual(evidence["source_file_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(evidence["changed_source_ids"]["rooms"], ["b_leaf_0"])
        self.assertEqual(evidence["synthesized_fixed_room_ids"], [])
        self.assertFalse(source["generation"]["room_solver_evidence"]["hierarchy_hints"]["positions_are_constraints"])
        self.assertTrue(job["qa"]["ok"])
        status, body, _ = self._request(f"/jobs/{edited}/check", {}, method="POST")
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["ok"])
        status, body, _ = self._request(f"/jobs/{edited}/artifacts/building_01_report.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertEqual(parent_path.read_bytes(), before)
        self.assertEqual(job["expert_acceptance"], "PENDING")

    def test_invalid_source_references_never_queue_jobs_or_accept_paths(self):
        for reference in ("../../outside.json", {}, {"job_id": "../outside", "variant": 1},
                          {"job_id": "a" * 32, "variant": True},
                          {"job_id": "a" * 32, "variant": 0},
                          {"job_id": "a" * 32, "variant": 1, "path": "outside.json"}):
            with self.subTest(reference=reference):
                payload = {**self.payload(), "incremental_from": reference}
                status, body, _ = self._request("/jobs", payload)
                self.assertEqual(status, 400, body)
        status, body, _ = self._request("/jobs", self.payload(prior="a" * 32))
        self.assertEqual(status, 404, body)
        self.assertEqual(json.loads(self._request("/jobs")[1])["jobs"], [])

    def test_changed_accepted_parent_is_rejected_before_an_edit_job_is_queued(self):
        prior = self.accepted_source()
        path = self.root / prior / "building_01.json"
        path.write_bytes(path.read_bytes() + b"\n")
        status, body, _ = self._request("/jobs", self.payload(edited=True, prior=prior))
        self.assertEqual(status, 409, body)
        jobs = json.loads(self._request("/jobs")[1])["jobs"]
        self.assertEqual(len(jobs), 1)

    def test_source_change_after_queue_retains_negative_evidence_without_artifact_publication(self):
        prior = self.accepted_source()
        service = FacilityService(self.root, start_worker=False)
        self.addCleanup(service.close)
        queued = service.submit(self.payload(edited=True, prior=prior))
        path = self.root / prior / "building_01.json"
        path.write_bytes(path.read_bytes() + b"\n")
        service._run(queued["id"])
        failed = service.get(queued["id"])
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["error"]["input_evidence"]["code"], "source_job_changed")
        self.assertEqual(failed["error"]["input_evidence"]["source_job_id"], prior)
        self.assertFalse(failed["error"]["input_evidence"]["proven_building_infeasible"])
        self.assertFalse((self.root / queued["id"] / "manifest.json").exists())
        self.assertNotIn("artifact_hashes", failed)

    def test_batch_resolves_all_sources_before_creating_any_job(self):
        prior = self.accepted_source()
        good = self.payload(edited=True, prior=prior)
        bad = self.payload(edited=True, prior="a" * 32)
        status, body, _ = self._request("/jobs/batch", {"jobs": [good, bad]})
        self.assertEqual(status, 404, body)
        self.assertEqual(len(json.loads(self._request("/jobs")[1])["jobs"]), 1)

    def test_failed_current_profile_keeps_source_identity_and_unknown_stage(self):
        prior = self.accepted_source()
        payload = self.payload(edited=True, prior=prior)
        payload["profile"] = FacilityProfile.from_mapping({
            "name": "Pressure evidence", "version": "P01", "rules": [{
                "id": "pressure", "kind": "pressure_cascade", "title": "Declared pressure ordering",
                "source": "Project pressure specification", "edition": "P01", "effective_date": "2026-10-12",
                "evidence": ["Confirm parent-child pressure edges"],
                "parameters": {"direction": "parent_gt_child"}}]}).to_dict()
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        job = self._finish(json.loads(body)["id"])
        self.assertEqual(job["status"], "FAILED", job)
        generation = job["error"]["generation"]
        self.assertEqual(generation["incremental_evidence"]["source_job_id"], prior)
        self.assertEqual(generation["incremental_evidence"]["synthesized_fixed_room_ids"], [])
        self.assertTrue(any(stage["stage"] == "facility" and stage["status"] == "UNKNOWN"
                            for stage in generation["rejected_candidates"][0]["stage_evidence"]))
        self.assertEqual(self._request(f"/jobs/{job['id']}/artifacts")[0], 409)

    def test_cache_identity_includes_accepted_parent_and_rechecks_its_hashes(self):
        prior = self.accepted_source()
        payload = {**self.payload(edited=True, prior=prior), "reuse": True}
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 202, body)
        edited = json.loads(body)["id"]
        self.assertEqual(self._finish(edited)["status"], "SUCCEEDED")
        status, body, _ = self._request("/jobs", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["id"], edited)
        second_parent = self.accepted_source()
        changed = {**payload, "incremental_from": {"job_id": second_parent, "variant": 1}}
        status, body, _ = self._request("/jobs", changed)
        self.assertEqual(status, 202, body)
        different = json.loads(body)["id"]
        self.assertNotEqual(different, edited)
        self.assertEqual(self._finish(different)["status"], "SUCCEEDED")
        path = self.root / prior / "building_01.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertEqual(self._request("/jobs", payload)[0], 409)


if __name__ == "__main__":
    unittest.main()
