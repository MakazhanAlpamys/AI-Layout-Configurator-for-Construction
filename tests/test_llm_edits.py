from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.proposed_edits import apply_proposed_edits
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout


class TypedEditProviderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.llm_edits"),
                             "Optional provider must return strict typed proposals")
        from layout_configurator.llm_edits import propose_with_compatible_provider
        self.propose = propose_with_compatible_provider
        self.program = BuildingIR.from_mapping({"version": "0.2", "layout": {
            "boundary": {"width": 10000, "height": 10000},
            "rooms": [{"id": "work", "area": 9, "min_area": 9, "max_area": 9}],
        }})
        self.requests = []
        self.status = 200
        self.content = json.dumps({"edits": [{"operation": "set_room", "target_id": "work",
            "changes": {key: {"value": 16, "unit": "m2"} for key in ("target_area", "min_area", "max_area")}}]})
        self.envelope = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append((json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                                       self.headers.get("Authorization")))
                body = json.dumps(owner.envelope if owner.envelope is not None else {
                    "choices": [{"message": {"content": owner.content}}]}).encode()
                self.send_response(owner.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _call(self, **changes):
        options = {"endpoint": f"http://127.0.0.1:{self.server.server_port}/chat/completions",
                   "model": "fixture-model", "api_key": "fixture-provider-key", "timeout_seconds": 2}
        options.update(changes)
        return self.propose(self.program, "Make work area exactly 16 m2", **options)

    def test_provider_proposal_is_confirmed_and_solved_as_new_canonical_input(self):
        draft = self._call()
        self.assertEqual(draft.state, "DRAFT")
        self.assertEqual(draft.source_kind, "ai_proposal")
        self.assertEqual(draft.producer, "fixture-model")
        self.assertEqual(draft.source_text, "Make work area exactly 16 m2")
        request, authorization = self.requests[0]
        self.assertEqual(authorization, "Bearer fixture-provider-key")
        prompt = request["messages"][-1]["content"]
        self.assertIn("work", prompt)
        self.assertIn("set_room", prompt)
        self.assertNotIn("fixture-provider-key", prompt)
        with self.assertRaisesRegex(ValueError, "confirmation"):
            apply_proposed_edits(self.program, draft)
        changed = apply_proposed_edits(self.program, draft, confirmed=True, reviewer="Program reviewer")
        result = solve_layouts(changed.building.layout, 1, time_limit_seconds=2)[0]
        self.assertEqual(result.placements["work"].area_m2, 16)
        self.assertTrue(validate_layout(changed.building.layout, result).ok)

    def test_provider_coordinates_and_verdicts_cannot_enter_a_draft(self):
        for response in ({"edits": [], "coordinates": {"work": [0, 0]}},
                         {"edits": [], "compliance": "PASS"},
                         {"edits": [{"operation": "set_room", "target_id": "work", "changes": {"x": 1000}}]}):
            with self.subTest(response=response):
                self.content = json.dumps(response)
                with self.assertRaises(ValueError):
                    self._call()

    def test_provider_edits_require_known_targets_and_exact_units(self):
        for target, unit in (("unknown", "m2"), ("work", "feet")):
            self.content = json.dumps({"edits": [{"operation": "set_room", "target_id": target,
                                                  "changes": {"target_area": {"value": 16, "unit": unit}}}]})
            with self.subTest(target=target, unit=unit), self.assertRaises(ValueError):
                self._call()

    def test_bad_provider_envelopes_and_http_failure_do_not_produce_a_draft(self):
        self.envelope = {"choices": []}
        with self.assertRaises(ValueError):
            self._call()
        self.status = 401
        with self.assertRaises(ValueError) as caught:
            self._call()
        self.assertNotIn("fixture-provider-key", str(caught.exception))

    def test_missing_opt_in_and_invalid_budget_never_call_the_provider(self):
        for options in ({"endpoint": ""}, {"model": ""}, {"api_key": ""}, {"timeout_seconds": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self._call(**options)
        self.assertEqual(self.requests, [])

    def test_cli_requests_a_provider_draft_without_applying_or_exporting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "program.json"
            original = json.dumps(self.program.to_dict()).encode()
            program.write_bytes(original)
            source = root / "request.txt"
            source.write_text("Make work area exactly 16 m2", encoding="utf-8")
            output = root / "proposal.json"
            env = {**os.environ, "LAYOUT_LLM_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}/chat/completions",
                   "LAYOUT_LLM_MODEL": "fixture-model", "LAYOUT_LLM_API_KEY": "fixture-provider-key"}
            result = subprocess.run([sys.executable, "-m", "layout_configurator.cli", "propose-llm-edits",
                                     str(program), str(source), "--output", str(output)],
                                    capture_output=True, text=True, env=env, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["state"], "DRAFT")
            self.assertEqual(program.read_bytes(), original)
            self.assertEqual(list(root.glob("*.ifc")), [])
            self.assertNotIn("fixture-provider-key", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
