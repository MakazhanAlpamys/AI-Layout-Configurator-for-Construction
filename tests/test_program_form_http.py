"""Real HTTP validation does not create solver jobs or leak protected data."""

import json
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.api import create_api_server
from layout_configurator.io import load_building


class ProgramFormHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = create_api_server(self.root, token="form-test")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.program = load_building(Path(__file__).resolve().parents[1] / "examples/pharma_cleanroom_pilot.yaml").to_dict()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, route, body=None, token="form-test"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + route, data=body, headers=headers), timeout=5) as result:
                return result.status, result.read(), result.headers
        except HTTPError as result:
            return result.code, result.read(), result.headers

    def test_validation_has_no_job_or_artifact_side_effects_and_returns_field_errors(self):
        before = set(self.root.rglob("*"))
        status, body, _ = self.request("/program/validate", {"program": self.program})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["program"], self.program)
        self.assertEqual(self.server.service.list(), [])
        self.assertEqual(set(self.root.rglob("*")), before)
        self.program["layout"]["rooms"][0]["min_width"] = "5000"
        status, body, _ = self.request("/program/validate", {"program": self.program})
        self.assertEqual(status, 400, body)
        self.assertIn("$.program.layout.rooms[0].min_width", [item["path"] for item in json.loads(body)["issues"]])
        self.assertEqual(self.server.service.list(), [])

    def test_config_and_validation_keep_the_existing_bearer_boundary(self):
        self.assertEqual(self.request("/program/config", token=None)[0], 401)
        self.assertEqual(self.request("/program/validate", {"program": self.program}, token=None)[0], 401)
        status, body, _ = self.request("/program/config")
        self.assertEqual(status, 200, body)
        config = json.loads(body)
        self.assertIn("layout", config["schemas"])
        self.assertIn("building", config["schemas"])
        self.assertIn("pharma-cleanroom", [item["id"] for item in config["profiles"]])
        self.assertEqual(config["defaults"]["rooms"], [])
        self.assertEqual(self.server.service.list(), [])

    def test_duplicate_json_keys_are_rejected_before_validation(self):
        status, body, _ = self.request("/program/validate", '{"program":{},"program":{}}')
        self.assertEqual(status, 400, body)
        self.assertIn("Duplicate JSON key", json.loads(body)["error"])

    def test_missing_project_names_are_input_errors_with_schema_paths(self):
        for layout_name in (False, True):
            program = copy.deepcopy(self.program)
            del (program["layout"] if layout_name else program)["project_name"]
            status, body, _ = self.request("/program/validate", {"program": program})
            self.assertEqual(status, 400, body)
            issues = json.loads(body)["issues"]
            self.assertTrue(any(issue["path"] == ("$.program.layout" if layout_name else "$.program") for issue in issues))

    def test_only_static_form_bootstrap_is_public_and_has_no_inline_executable_code(self):
        for route, content_type, marker in (("/", "text/html", b"Facility program"),
                                            ("/program.js", "javascript", b"/program/validate"),
                                            ("/program.css", "text/css", b"font-family")):
            with self.subTest(route=route):
                status, body, headers = self.request(route, token=None)
                self.assertEqual(status, 200, body)
                self.assertIn(content_type, headers["Content-Type"])
                self.assertIn(marker, body)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/static/../../api.py", token=None)[0], 401)
        self.assertEqual(self.request("/jobs", token=None)[0], 401)
        status, body, _ = self.request("/favicon.ico", token=None)
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        body = self.request("/", token=None)[1]
        self.assertNotIn(b"<script>", body)
        self.assertNotIn(b"local-form-test", body)
