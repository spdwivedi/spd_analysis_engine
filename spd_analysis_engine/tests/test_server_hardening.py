"""Regression tests verifying EngineWebServer and control channel security hardening."""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from scripts.server.security import (
    MAX_REQUEST_BODY_SIZE, get_cors_headers, is_trusted_origin, is_valid_host, sanitize_error_message,
)
from scripts.web_server import EngineWebServer


def _raw_http_request(
    host: str,
    port: int,
    raw_request: str,
) -> tuple[int, dict[str, str], str]:
    """Execute raw HTTP request over socket to precisely test malformed/custom headers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect((host, port))
    try:
        sock.sendall(raw_request.encode("utf-8"))
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                # Check for Content-Length or end
                header_part, _, body_part = response.partition(b"\r\n\r\n")
                lines = header_part.split(b"\r\n")
                status_line = lines[0].decode("utf-8", errors="replace")
                headers = {}
                for h in lines[1:]:
                    if b":" in h:
                        k, v = h.decode("utf-8", errors="replace").split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                content_len = int(headers.get("content-length", 0))
                if len(body_part) >= content_len:
                    break
        raw_text = response.decode("utf-8", errors="replace")
        header_text, _, body_text = raw_text.partition("\r\n\r\n")
        status_code = int(header_text.splitlines()[0].split()[1]) if header_text else 500
        headers_dict = {}
        for line in header_text.splitlines()[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers_dict[k.strip().lower()] = v.strip()
        return status_code, headers_dict, body_text
    finally:
        sock.close()


class TestServerHardening(unittest.TestCase):
    """Test suite for server security hardening."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_harden_test_"))
        cls.web_dir = cls.temp_dir / "web"
        cls.web_dir.mkdir(parents=True, exist_ok=True)
        (cls.web_dir / "index.html").write_text(
            "<!DOCTYPE html><html><head><title>SPD Test</title></head><body><h1>SPD</h1></body></html>",
            encoding="utf-8",
        )
        cls.fixed_token = "secure_test_token_9876543210_abcdefg"
        cls.server = EngineWebServer(
            engine_root=cls.temp_dir,
            host="127.0.0.1",
            port=0,
            web_dir=cls.web_dir,
            api_token=cls.fixed_token,
            require_token=True,
        )
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def _http_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | str | bytes | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Convenience method using urllib for structured requests."""
        url = f"{self.base_url}{path}"
        req_headers = {"Host": f"127.0.0.1:{self.port}"}
        if headers:
            req_headers.update(headers)

        payload_bytes: bytes | None = None
        if data is not None:
            if isinstance(data, dict):
                payload_bytes = json.dumps(data).encode("utf-8")
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"
            elif isinstance(data, str):
                payload_bytes = data.encode("utf-8")
            elif isinstance(data, bytes):
                payload_bytes = data

        req = urllib.request.Request(url, data=payload_bytes, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                body = resp.read().decode("utf-8", errors="replace")
                return status, resp_headers, body
        except urllib.error.HTTPError as err:
            with err:
                resp_headers = {k.lower(): v for k, v in err.headers.items()}
                body = err.read().decode("utf-8", errors="replace")
                return err.code, resp_headers, body

    # ------------------------------------------------------------------
    # 1. Host Allowlist Tests
    # ------------------------------------------------------------------

    def test_wrong_host_header_rejected(self) -> None:
        """Verify unexpected Host header values are rejected with 400 Bad Request."""
        # 1a. Domain name not in allowlist
        raw_req = (
            f"GET /api/status HTTP/1.1\r\n"
            f"Host: evil.com\r\n"
            f"Connection: close\r\n\r\n"
        )
        status, _, body = _raw_http_request("127.0.0.1", self.port, raw_req)
        self.assertEqual(status, 400)
        self.assertIn("Host header", body)

        # 1b. Domain with matching port
        raw_req_port = (
            f"GET /api/status HTTP/1.1\r\n"
            f"Host: attacker.org:{self.port}\r\n"
            f"Connection: close\r\n\r\n"
        )
        status, _, _ = _raw_http_request("127.0.0.1", self.port, raw_req_port)
        self.assertEqual(status, 400)

        # 1c. Mismatched port on localhost
        raw_req_wrong_port = (
            f"GET /api/status HTTP/1.1\r\n"
            f"Host: 127.0.0.1:9999\r\n"
            f"Connection: close\r\n\r\n"
        )
        status, _, _ = _raw_http_request("127.0.0.1", self.port, raw_req_wrong_port)
        self.assertEqual(status, 400)

        # 1d. Missing Host header entirely
        raw_req_no_host = (
            f"GET /api/status HTTP/1.1\r\n"
            f"Connection: close\r\n\r\n"
        )
        status, _, _ = _raw_http_request("127.0.0.1", self.port, raw_req_no_host)
        self.assertEqual(status, 400)

    def test_valid_host_headers_accepted(self) -> None:
        """Verify standard localhost and 127.0.0.1 Host headers succeed."""
        for valid_host in [
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
            "127.0.0.1",
            "localhost",
        ]:
            raw_req = (
                f"GET /api/status HTTP/1.1\r\n"
                f"Host: {valid_host}\r\n"
                f"Connection: close\r\n\r\n"
            )
            status, _, body = _raw_http_request("127.0.0.1", self.port, raw_req)
            self.assertEqual(status, 200, f"Host '{valid_host}' should be accepted")

    # ------------------------------------------------------------------
    # 2. Malicious Origin & CORS Tests
    # ------------------------------------------------------------------

    def test_malicious_origin_rejected_on_mutations(self) -> None:
        """Verify state-changing requests with untrusted Origin return 403 Forbidden."""
        for bad_origin in [
            "http://evil.com",
            "https://attacker.org",
            "http://evil.com:8765",
            f"http://localhost:9999",  # wrong port
            "null",
        ]:
            status, headers, body = self._http_request(
                "POST",
                "/api/git/test",
                headers={
                    "Origin": bad_origin,
                    "X-API-Token": self.fixed_token,
                    "Content-Type": "application/json",
                },
                data={"remote_url": "https://github.com/test/repo"},
            )
            self.assertEqual(status, 403, f"Origin '{bad_origin}' should return 403 Forbidden")
            self.assertIn("untrusted origin", body)
            # Ensure no wildcard or echoed origin in CORS headers
            self.assertNotIn("access-control-allow-origin", headers)

    def test_no_wildcard_cors(self) -> None:
        """Verify Access-Control-Allow-Origin NEVER outputs wildcard '*'."""
        # Check GET /api/status
        status, headers, _ = self._http_request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotEqual(headers.get("access-control-allow-origin"), "*")

        # Check OPTIONS preflight with untrusted origin
        status, headers, _ = self._http_request(
            "OPTIONS",
            "/api/projects/start",
            headers={"Origin": "http://evil.com"},
        )
        self.assertEqual(status, 403)
        self.assertNotIn("access-control-allow-origin", headers)

    # ------------------------------------------------------------------
    # 3. Missing Token Tests
    # ------------------------------------------------------------------

    def test_missing_token_rejected_on_mutations(self) -> None:
        """Verify mutation requests without an API token fail with 401 Unauthorized."""
        # 3a. POST /api/git/test
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={"Content-Type": "application/json"},
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 401)
        self.assertIn("missing API token", body)

        # 3b. POST /api/trash/purge-all (empty body)
        status, _, body = self._http_request("POST", "/api/trash/purge-all")
        self.assertEqual(status, 401)
        self.assertIn("missing API token", body)

        # 3c. DELETE /api/trash/purge-all
        status, _, body = self._http_request("DELETE", "/api/trash/purge-all")
        self.assertEqual(status, 401)
        self.assertIn("missing API token", body)

    # ------------------------------------------------------------------
    # 4. Invalid Token Tests
    # ------------------------------------------------------------------

    def test_invalid_token_rejected_on_mutations(self) -> None:
        """Verify mutation requests with incorrect API tokens fail with 401 Unauthorized."""
        # Via X-API-Token header
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "X-API-Token": "completely_wrong_token_123456",
                "Content-Type": "application/json",
            },
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 401)
        self.assertIn("invalid API token", body)

        # Via Authorization: Bearer header
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "Authorization": "Bearer fake_token_abc",
                "Content-Type": "application/json",
            },
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 401)
        self.assertIn("invalid API token", body)

    # ------------------------------------------------------------------
    # 5. Non-JSON Mutation Body Tests
    # ------------------------------------------------------------------

    def test_non_json_mutation_body_rejected(self) -> None:
        """Verify POST/PUT with non-JSON Content-Type fails with 415 Unsupported Media Type."""
        # 5a. text/plain
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "X-API-Token": self.fixed_token,
                "Content-Type": "text/plain",
            },
            data="raw text data",
        )
        self.assertEqual(status, 415)
        self.assertIn("application/json", body)

        # 5b. application/x-www-form-urlencoded
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "X-API-Token": self.fixed_token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="key=value&foo=bar",
        )
        self.assertEqual(status, 415)
        self.assertIn("application/json", body)

    # ------------------------------------------------------------------
    # 6. Oversized Body Tests
    # ------------------------------------------------------------------

    def test_oversized_body_rejected(self) -> None:
        """Verify payload exceeding MAX_REQUEST_BODY_SIZE is rejected with 413 Payload Too Large."""
        # Exceed MAX_REQUEST_BODY_SIZE via Content-Length header
        raw_req = (
            f"POST /api/git/test HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"X-API-Token: {self.fixed_token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {MAX_REQUEST_BODY_SIZE + 1024}\r\n"
            f"Connection: close\r\n\r\n"
        )
        status, _, body = _raw_http_request("127.0.0.1", self.port, raw_req)
        self.assertEqual(status, 413)
        self.assertIn("Payload Too Large", body)

    # ------------------------------------------------------------------
    # 7. Normal Same-Origin Request Tests
    # ------------------------------------------------------------------

    def test_normal_same_origin_request_succeeds(self) -> None:
        """Verify normal same-origin browser request succeeds and receives CORS / token headers."""
        trusted_origin = f"http://127.0.0.1:{self.port}"

        # 7a. GET index.html receives token meta tag and SameSite cookie
        status, headers, body = self._http_request(
            "GET",
            "/",
            headers={"Origin": trusted_origin},
        )
        self.assertEqual(status, 200)
        self.assertIn(f'content="{self.fixed_token}"', body)
        self.assertIn("spd_token=", headers.get("set-cookie", ""))
        self.assertEqual(headers.get("access-control-allow-origin"), trusted_origin)

        # 7b. GET /api/auth/token endpoint returns token for same-origin JS
        status, _, body = self._http_request(
            "GET",
            "/api/auth/token",
            headers={"Origin": trusted_origin},
        )
        self.assertEqual(status, 200)
        auth_data = json.loads(body)
        self.assertEqual(auth_data.get("token"), self.fixed_token)

        # 7c. Mutation with Cookie spd_token succeeds
        status, headers, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "Origin": trusted_origin,
                "Cookie": f"spd_token={self.fixed_token}",
                "Content-Type": "application/json",
            },
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), trusted_origin)

    # ------------------------------------------------------------------
    # 8. Normal CLI/API Request Tests
    # ------------------------------------------------------------------

    def test_normal_cli_api_request_succeeds(self) -> None:
        """Verify normal CLI/API client without Origin header succeeds with valid token."""
        # 8a. Using X-API-Token header
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "X-API-Token": self.fixed_token,
                "Content-Type": "application/json",
            },
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 200)
        res_data = json.loads(body)
        self.assertIn("success", res_data)

        # 8b. Using Authorization: Bearer <token>
        status, _, body = self._http_request(
            "POST",
            "/api/git/test",
            headers={
                "Authorization": f"Bearer {self.fixed_token}",
                "Content-Type": "application/json",
            },
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 200)
        res_data = json.loads(body)
        self.assertIn("success", res_data)

    # ------------------------------------------------------------------
    # 9. No Tokens in URLs (Security Invariant)
    # ------------------------------------------------------------------

    def test_token_in_url_query_param_is_not_accepted(self) -> None:
        """Verify passing token as URL query parameter ?token=... is ignored and rejected with 401."""
        status, _, body = self._http_request(
            "POST",
            f"/api/git/test?token={self.fixed_token}&api_key={self.fixed_token}",
            headers={"Content-Type": "application/json"},
            data={"remote_url": "https://github.com/test/repo"},
        )
        self.assertEqual(status, 401)
        self.assertIn("missing API token", body)

    # ------------------------------------------------------------------
    # 10. Safe Error Responses (Information Leakage Prevention)
    # ------------------------------------------------------------------

    def test_safe_error_responses_redact_paths_and_secrets(self) -> None:
        """Verify error messages sanitize filesystem paths and secrets."""
        raw_msg = "Failed in D:\\Project_Files\\SPD_Analysis_Engine\\secret\\key.txt with AIzaSy0123456789ABCDEF"
        sanitized = sanitize_error_message(raw_msg)
        self.assertNotIn("D:\\Project_Files", sanitized)
        self.assertNotIn("AIzaSy0123456789ABCDEF", sanitized)
        self.assertIn("[path]", sanitized)
        self.assertIn("[secret]", sanitized)

    def test_unauthenticated_token_endpoint_rejected(self) -> None:
        """Verify unauthenticated GET /api/auth/token without Origin or Token is rejected."""
        status, _, body = self._http_request("GET", "/api/auth/token")
        self.assertEqual(status, 401)
        status, _, body = self._http_request("GET", "/api/auth/token", headers={"Origin": "http://evil.com"})
        self.assertEqual(status, 403)

    def test_worker_control_channel_security(self) -> None:
        """Verify worker loopback control channel rejects wrong Host, Origin, and bad token."""
        from scripts.controller.control_channel import WorkerControlServer
        ctrl = WorkerControlServer(secret="sec123", project_dir=self.temp_dir / "wctrl_t")
        ctrl.start()
        try:
            url = f"http://127.0.0.1:{ctrl.port}/"
            # 1. Wrong Host -> 400
            req = urllib.request.Request(url, data=b"{}", headers={"Host": "evil.com", "Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(cm.exception.code, 400)

            # 2. Untrusted Origin -> 403
            req = urllib.request.Request(url, data=b"{}", headers={"Host": f"127.0.0.1:{ctrl.port}", "Origin": "http://evil.com", "Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(cm.exception.code, 403)

            # 3. Non-JSON -> 415
            req = urllib.request.Request(url, data=b"txt", headers={"Host": f"127.0.0.1:{ctrl.port}", "Content-Type": "text/plain"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(cm.exception.code, 415)

            # 4. Missing/invalid secret -> 401
            p = json.dumps({"protocol": 1, "command": "PING", "request_id": "t"}).encode("utf-8")
            req = urllib.request.Request(url, data=p, headers={"Host": f"127.0.0.1:{ctrl.port}", "Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(cm.exception.code, 401)
        finally:
            ctrl.stop()


if __name__ == "__main__":
    unittest.main()
