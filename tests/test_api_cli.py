import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen


class ApiCliTests(unittest.TestCase):
    def test_cli_starts_a_real_authenticated_api_with_persistent_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
            env = {**os.environ, "LAYOUT_API_TOKEN": "cli-fixture-token", "PYTHONUNBUFFERED": "1"}
            process = subprocess.Popen(
                [sys.executable, "-m", "layout_configurator.cli", "api", "--output", directory,
                 "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                ready = None
                deadline = time.monotonic() + 10
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with urlopen(Request(f"http://127.0.0.1:{port}/health",
                                             headers={"Authorization": "Bearer cli-fixture-token"}), timeout=0.3) as response:
                            ready = json.load(response)
                        break
                    except OSError:
                        time.sleep(0.05)
                self.assertIsNotNone(ready, "CLI did not expose the generation API")
                self.assertEqual(ready["status"], "ok")
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
            self.assertNotIn(b"cli-fixture-token", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
