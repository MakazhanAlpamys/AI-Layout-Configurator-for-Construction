"""Opted HTTP diagnostics stay JSON-persistent, source-bound and cache-specific."""

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.api import FacilityService, _validated_request, create_api_server
from test_zone_conflict_delivery import _packing_conflict
from test_zone_constraints import _single


def request_payload(**options):
    return {"program": _single().to_dict(), "room_solver": "zone_hierarchical",
            "variants": 1, "max_attempts": 1, "time_limit_seconds": .5,
            "deterministic_units": .05, "seed": 1, **options}


class ZoneConflictApiTests(unittest.TestCase):
    def admit(self, payload):
        try:
            return _validated_request(payload)
        except ValueError as exc:
            self.fail("HTTP contract cannot retain opted conflict diagnostics: " + str(exc))

    def test_default_and_explicit_false_are_byte_equal_and_custom_budget_is_json_only(self):
        before = self.admit(request_payload())
        disabled = self.admit(request_payload(explain_zone_conflicts=False))
        self.assertEqual(before, disabled)
        self.assertNotIn("explain_zone_conflicts", disabled["settings"])
        budget = {"wall_seconds": 2, "max_solves": 8, "deterministic_units": .05}
        accepted = self.admit(request_payload(explain_zone_conflicts=True, zone_conflict_budget=budget))
        self.assertEqual(accepted["settings"]["zone_conflict_budget"], budget)
        self.assertIs(accepted["settings"]["explain_zone_conflicts"], True)
        self.assertEqual(json.loads(json.dumps(accepted)), accepted)
        budget["max_solves"] = 1
        self.assertEqual(accepted["settings"]["zone_conflict_budget"]["max_solves"], 8)

    def test_invalid_or_incremental_controls_never_queue_and_batch_is_atomic(self):
        invalid = [request_payload(explain_zone_conflicts=value) for value in (1, "true", None)]
        invalid += [request_payload(zone_conflict_budget={"wall_seconds": 2}),
                    request_payload(explain_zone_conflicts=True, room_solver="auto"),
                    request_payload(explain_zone_conflicts=True, incremental_from={"job_id": "a"*32, "variant": 1})]
        invalid += [request_payload(explain_zone_conflicts=True, zone_conflict_budget=value) for value in
                    ({}, None, {"wall_seconds": True}, {"max_solves": True}, {"max_solves": 513},
                     {"wall_seconds": 11}, {"deterministic_units": 0}, {"unknown": 1})]
        with tempfile.TemporaryDirectory() as directory:
            service = FacilityService(directory, start_worker=False)
            for payload in invalid:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    service.submit(payload)
            self.assertEqual(service.list(), [])
            with self.assertRaises(ValueError):
                service.submit_batch({"jobs": [request_payload(explain_zone_conflicts=True), invalid[0]]})
            self.assertEqual(service.list(), [])

    def test_cache_identity_preserves_defaults_and_distinguishes_explicit_diagnostic_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FacilityService(directory, start_worker=False)
            try:
                jobs = [service.submit(request_payload(**options)) for options in (
                    {}, {"explain_zone_conflicts": False}, {"explain_zone_conflicts": True},
                    {"explain_zone_conflicts": True, "zone_conflict_budget": {"max_solves": 8}},
                    {"explain_zone_conflicts": True, "zone_conflict_budget": {"max_solves": 9}})]
            except ValueError as exc:
                self.fail("Diagnostic controls cannot participate in persisted cache identity: " + str(exc))
            self.assertEqual(jobs[0]["cache_key"], jobs[1]["cache_key"])
            self.assertEqual(len({job["cache_key"] for job in jobs[1:]}), 4)
            for job in jobs:
                request = json.loads((Path(directory) / job["id"] / "request.json").read_text())
                self.assertEqual(request["settings"], job["settings"])

    def test_equivalent_partial_budgets_share_identity_and_numeric_limits_are_strict(self):
        implicit = self.admit(request_payload(explain_zone_conflicts=True))
        explicit = self.admit(request_payload(explain_zone_conflicts=True, zone_conflict_budget={
            "wall_seconds": 2.0, "max_solves": 64, "deterministic_units": .1}))
        self.assertEqual(implicit, explicit)
        boundary = self.admit(request_payload(explain_zone_conflicts=True, zone_conflict_budget={
            "wall_seconds": 10, "max_solves": 512, "deterministic_units": None}))
        self.assertIsNone(boundary["settings"]["zone_conflict_budget"]["deterministic_units"])
        for budget in ({"wall_seconds": 0}, {"max_solves": 2.0}, {"deterministic_units": False},
                       {"deterministic_units": 10.01}, {"wall_seconds": float("nan")},
                       {"deterministic_units": float("inf")}):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                _validated_request(request_payload(explain_zone_conflicts=True, zone_conflict_budget=budget))

    @contextmanager
    def start_http(self, root):
        server = create_api_server(root, ("127.0.0.1", 0), token="conflict-fixture")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def http(self, base, path, payload=None):
        request = Request(base + path, headers={"Authorization": "Bearer conflict-fixture", "Content-Type": "application/json"},
            data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def finish(self, base, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, job = self.http(base, "/jobs/" + job_id)
            self.assertEqual(status, 200)
            if job["status"] in ("SUCCEEDED", "FAILED"):
                return job
            time.sleep(.02)
        self.fail("Bounded tiny diagnostic HTTP job did not finish")

    def test_actual_http_failure_retains_json_source_and_unknown_base_explanation_after_restart(self):
        with tempfile.TemporaryDirectory() as directory, self.start_http(directory) as base:
            payload = request_payload(program=_packing_conflict().to_dict(), explain_zone_conflicts=True,
                                      zone_conflict_budget={"wall_seconds": 2, "max_solves": 8})
            status, queued = self.http(base, "/jobs", payload)
            self.assertEqual(status, 202, queued)
            job = self.finish(base, queued["id"])
            self.assertEqual(job["status"], "FAILED", job)
            generation = job["error"]["generation"]
            proof = generation["room_solver_evidence"]["conflict_explanations"]["explanations"][0]["report"]
            self.assertEqual((proof["status"], proof["reason"]), ("UNKNOWN", "base_model_infeasible"))
            self.assertFalse(proof["core"]["subset_minimal"])
            self.assertFalse(proof["proven_building_infeasible"])
            disk = json.loads((Path(directory) / job["id"] / "generation-failure.json").read_text())
            self.assertEqual(disk["spec"], payload["program"])
            self.assertEqual(disk["generation"], generation)
            reader = FacilityService(directory, start_worker=False)
            self.assertEqual(reader.get(job["id"]), job)
            self.assertEqual(job["settings"]["zone_conflict_budget"]["max_solves"], 8)

    def test_actual_http_accepted_snapshot_is_reusable_only_after_saved_qa(self):
        with tempfile.TemporaryDirectory() as directory, self.start_http(directory) as base:
            payload = request_payload(explain_zone_conflicts=True, zone_conflict_budget={"max_solves": 8}, reuse=True)
            status, queued = self.http(base, "/jobs", payload)
            self.assertEqual(status, 202, queued)
            job = self.finish(base, queued["id"])
            self.assertEqual(job["status"], "SUCCEEDED", job)
            manifest = json.loads((Path(directory) / job["id"] / "manifest.json").read_text())
            evidence = manifest["generation"]["room_solver_evidence"]["conflict_explanations"]
            self.assertEqual(evidence["solves_used"], 0)
            self.assertEqual(evidence["captures"][0]["capture"]["capture_status"], "COMPLETE")
            status, qa = self.http(base, "/jobs/" + job["id"] + "/check", {})
            self.assertEqual(status, 200, qa)
            self.assertTrue(qa["ok"])
            status, cached = self.http(base, "/jobs", payload)
            self.assertEqual(status, 200, cached)
            self.assertEqual(cached["id"], job["id"])
            self.assertTrue(cached["cache_hit"])
