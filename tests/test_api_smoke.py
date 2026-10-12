from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import struct
import threading
import unittest
from urllib.request import urlopen

import scripts.api_smoke as smoke


class ApiSmokeStartupTests(unittest.TestCase):
    def test_startup_connection_reset_is_retried_before_submitting_a_pilot(self):
        attempts = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                attempts.append(self.path)
                if len(attempts) == 1:
                    # Docker forwards an early connection before the Python
                    # listener is ready; reproduce its TCP reset on a real port.
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.connection.close()
                    return
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(path):
                with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=2) as response:
                    return json.load(response)

            self.assertTrue(callable(getattr(smoke, "_wait_for_health", None)), "API smoke must tolerate startup TCP resets")
            result = smoke._wait_for_health(request, timeout=2)
            self.assertEqual(result, {"status": "ok"})
            self.assertEqual(attempts, ["/health", "/health"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
