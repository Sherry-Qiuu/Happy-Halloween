"""Tests for accounts, sessions, per-user data, and the HTTP auth API.

Run with ``python -m unittest discover -s tests`` from the project root.
The tests use a temporary directory inside the project (the DSH sandbox
does not grant write access to the system temp directory).
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import accounts  # noqa: E402
import server  # noqa: E402
from config import load_settings  # noqa: E402


def _temp_dir(prefix: str) -> Path:
    """Create a directory the DSH sandbox can still write into.

    ``tempfile.mkdtemp`` applies a private mode that the Windows sandbox
    cannot traverse; a plain mkdir inherits the workspace permissions.
    """
    path = ROOT / f".{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


class AccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = _temp_dir("accounts")
        self.store = accounts.AccountStore(self.temp)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_register_and_authenticate(self) -> None:
        user = self.store.create_user(
            "Alice", "correct-horse", "Alice", email="alice@example.com"
        )
        self.assertEqual(user["username"], "alice")
        self.assertEqual(user["display_name"], "Alice")
        self.assertEqual(user["email"], "alice@example.com")
        self.assertTrue(user["email_verified"] is False)
        self.assertNotIn("password_hash", user)

        found = self.store.authenticate("ALICE", "correct-horse")
        self.assertEqual(found["id"], user["id"])

    def test_email_is_normalized_and_unique(self) -> None:
        self.store.create_user(
            "alice", "correct-horse", email="Alice@Example.COM"
        )
        record = self.store.find_by_email("alice@example.com")
        self.assertEqual(record["username"], "alice")
        with self.assertRaises(accounts.AccountError) as taken:
            self.store.create_user(
                "bob", "correct-horse", email="ALICE@example.com"
            )
        self.assertEqual(taken.exception.status, 409)
        self.assertEqual(taken.exception.code, "email_taken")

        for invalid in ("not-an-email", "a@b", "@example.com", "a..b@x.com"):
            with self.assertRaises(accounts.AccountError):
                accounts.validate_email(invalid)

    def test_password_reset_by_email(self) -> None:
        user = self.store.create_user(
            "alice",
            "correct-horse",
            email="alice@example.com",
            email_verified=True,
        )
        token = self.store.create_session(user["id"])
        self.store.reset_password_by_email(
            "ALICE@example.com", "new-correct-horse"
        )
        self.assertIsNone(self.store.resolve_session(token))
        with self.assertRaises(accounts.AccountError):
            self.store.authenticate("alice", "correct-horse")
        self.assertEqual(
            self.store.authenticate("alice", "new-correct-horse")["id"],
            user["id"],
        )

    def test_duplicate_and_weak_credentials_are_rejected(self) -> None:
        self.store.create_user("alice", "correct-horse")
        with self.assertRaises(accounts.AccountError) as taken:
            self.store.create_user("ALICE", "another-password")
        self.assertEqual(taken.exception.status, 409)

        with self.assertRaises(accounts.AccountError):
            self.store.create_user("bob", "short")
        with self.assertRaises(accounts.AccountError):
            self.store.create_user("bad name", "correct-horse")
        with self.assertRaises(accounts.AccountError):
            self.store.create_user("x", "correct-horse")

    def test_wrong_password_is_generic(self) -> None:
        self.store.create_user("alice", "correct-horse")
        with self.assertRaises(accounts.AccountError) as wrong:
            self.store.authenticate("alice", "wrong-password")
        self.assertEqual(wrong.exception.status, 401)
        with self.assertRaises(accounts.AccountError) as missing:
            self.store.authenticate("nobody", "wrong-password")
        self.assertEqual(missing.exception.status, 401)

    def test_session_lifecycle(self) -> None:
        user = self.store.create_user("alice", "correct-horse")
        token = self.store.create_session(user["id"])
        self.assertEqual(self.store.resolve_session(token)["id"], user["id"])
        self.assertIsNone(self.store.resolve_session(token + "x"))

        self.store.revoke_session(token)
        self.assertIsNone(self.store.resolve_session(token))

    def test_password_change_ends_old_sessions(self) -> None:
        user = self.store.create_user("alice", "correct-horse")
        token = self.store.create_session(user["id"])

        self.store.change_password(
            user["id"], "correct-horse", "new-correct-horse"
        )
        self.assertIsNone(self.store.resolve_session(token))
        with self.assertRaises(accounts.AccountError):
            self.store.authenticate("alice", "correct-horse")
        self.assertEqual(
            self.store.authenticate("alice", "new-correct-horse")["id"],
            user["id"],
        )

    def test_data_is_per_user(self) -> None:
        alice = self.store.create_user("alice", "correct-horse")
        bob = self.store.create_user("bob", "correct-horse")
        self.assertEqual(self.store.load_data(alice["id"]), (None, None))

        self.store.save_data(alice["id"], {"tasks": [{"id": 1}]})
        data, updated = self.store.load_data(alice["id"])
        self.assertEqual(data, {"tasks": [{"id": 1}]})
        self.assertTrue(updated)
        self.assertEqual(self.store.load_data(bob["id"]), (None, None))

    def test_delete_removes_data_and_account(self) -> None:
        user = self.store.create_user("alice", "correct-horse")
        self.store.save_data(user["id"], {"tasks": []})
        self.store.delete_user(user["id"], "correct-horse")
        self.assertIsNone(self.store.get_user(user["id"]))
        with self.assertRaises(accounts.AccountError):
            self.store.authenticate("alice", "correct-horse")

    def test_state_survives_a_restart(self) -> None:
        user = self.store.create_user("alice", "correct-horse")
        token = self.store.create_session(user["id"])
        self.store.save_data(user["id"], {"tasks": [{"id": 1}]})

        reopened = accounts.AccountStore(self.temp)
        self.assertEqual(reopened.resolve_session(token)["id"], user["id"])
        data, _ = reopened.load_data(user["id"])
        self.assertEqual(data, {"tasks": [{"id": 1}]})

    def test_data_size_limit(self) -> None:
        user = self.store.create_user("alice", "correct-horse")
        payload = {"blob": "x" * (accounts.MAX_DATA_BYTES + 1)}
        with self.assertRaises(accounts.AccountError) as too_big:
            self.store.save_data(user["id"], payload)
        self.assertEqual(too_big.exception.status, 413)


class FakeMailer:
    """Capture verification codes instead of talking to SMTP."""

    def __init__(self) -> None:
        self.sent: dict[tuple[str, str], str] = {}

    @property
    def configured(self) -> bool:
        return True

    def send_code(self, to_email: str, code: str, purpose: str) -> None:
        self.sent[(to_email, purpose)] = code


class HttpAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = _temp_dir("http")
        base = load_settings()
        settings = replace(
            base,
            host="127.0.0.1",
            port=0,
            mock=True,
            data_dir=cls.temp,
            allow_registration=True,
            cookie_secure=False,
            session_days=7,
            max_users=100,
        )
        cls.mailer = FakeMailer()
        cls.httpd = server.create_server(settings, mailer=cls.mailer)
        # This suite is not about rate limiting; let its many requests
        # through so the functional assertions stay readable.
        for name in (
            "register_ip",
            "login_ip",
            "login_user",
            "email_code_ip",
            "reset_ip",
        ):
            setattr(
                cls.httpd,
                name,
                server.RateLimiter(10_000, server.AUTH_WINDOW),
            )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.temp, ignore_errors=True)

    def setUp(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.username = "user_" + uuid.uuid4().hex[:8]

    def call(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = None
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body or "{}")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, json.loads(body or "{}")

    def request_code(
        self,
        email: str,
        purpose: str = "register",
        username: str | None = None,
    ) -> str:
        payload: dict = {"email": email, "purpose": purpose}
        if username:
            payload["username"] = username
        status, body = self.call("POST", "/api/auth/email-code", payload)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["sent"])
        key = (email.strip().lower(), purpose)
        self.assertIn(key, self.mailer.sent, body)
        return self.mailer.sent[key]

    def register(self, username: str | None = None) -> dict:
        username = username or self.username
        email = f"{username}@example.com"
        code = self.request_code(email, "register", username)
        status, body = self.call(
            "POST",
            "/api/auth/register",
            {
                "username": username,
                "password": "correct-horse",
                "display_name": "Alice",
                "email": email,
                "code": code,
            },
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["user"]["email"], email)
        self.assertTrue(body["user"]["email_verified"])
        return body["user"]

    def test_health(self) -> None:
        status, body = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_requires_login(self) -> None:
        status, body = self.call("GET", "/api/auth/me")
        self.assertEqual(status, 401)
        self.assertEqual(body["code"], "unauthenticated")
        status, _ = self.call("GET", "/api/data")
        self.assertEqual(status, 401)

    def test_register_login_logout(self) -> None:
        user = self.register()
        status, body = self.call("GET", "/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["id"], user["id"])

        status, _ = self.call("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)
        status, _ = self.call("GET", "/api/auth/me")
        self.assertEqual(status, 401)

        status, body = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "correct-horse"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["id"], user["id"])

    def test_data_roundtrip_and_isolation(self) -> None:
        self.register("alice")
        status, body = self.call("GET", "/api/data")
        self.assertEqual(status, 200)
        self.assertIsNone(body["data"])

        document = {"tasks": [{"id": 1, "title": "write"}], "name": "Alice"}
        status, body = self.call("PUT", "/api/data", {"data": document})
        self.assertEqual(status, 200)
        self.assertTrue(body["updated_at"])
        status, body = self.call("GET", "/api/data")
        self.assertEqual(body["data"], document)

        self.jar.clear()
        self.register("bob_" + uuid.uuid4().hex[:8])
        status, body = self.call("GET", "/api/data")
        self.assertIsNone(body["data"])

    def test_profile_and_password_change(self) -> None:
        self.register()
        status, body = self.call(
            "PATCH", "/api/account", {"display_name": "小明"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["display_name"], "小明")

        status, _ = self.call(
            "POST",
            "/api/account/password",
            {
                "current_password": "correct-horse",
                "new_password": "new-correct-horse",
            },
        )
        self.assertEqual(status, 200)
        status, _ = self.call("GET", "/api/auth/me")
        self.assertEqual(status, 200)

        self.jar.clear()
        status, _ = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "correct-horse"},
        )
        self.assertEqual(status, 401)
        status, _ = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "new-correct-horse"},
        )
        self.assertEqual(status, 200)

    def test_cross_site_request_is_rejected(self) -> None:
        status, body = self.call(
            "POST",
            "/api/auth/register",
            {"username": "mallory", "password": "correct-horse"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "origin_denied")

    def test_register_requires_email_and_code(self) -> None:
        status, body = self.call(
            "POST",
            "/api/auth/register",
            {"username": self.username, "password": "correct-horse"},
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "email_invalid")

        email = f"{self.username}@example.com"
        status, body = self.call(
            "POST",
            "/api/auth/register",
            {
                "username": self.username,
                "password": "correct-horse",
                "email": email,
                "code": "123456",
            },
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "code_expired")

    def test_duplicate_email_is_rejected(self) -> None:
        user = self.register()
        status, body = self.call(
            "POST",
            "/api/auth/email-code",
            {
                "email": user["email"],
                "purpose": "register",
                "username": "other_" + uuid.uuid4().hex[:6],
            },
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body["code"], "email_taken")

    def test_wrong_code_is_rejected_then_correct_code_works(self) -> None:
        email = f"{self.username}@example.com"
        code = self.request_code(email, "register", self.username)
        wrong = "000000" if code != "000000" else "111111"
        status, body = self.call(
            "POST",
            "/api/auth/register",
            {
                "username": self.username,
                "password": "correct-horse",
                "email": email,
                "code": wrong,
            },
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "code_mismatch")

        status, body = self.call(
            "POST",
            "/api/auth/register",
            {
                "username": self.username,
                "password": "correct-horse",
                "email": email,
                "code": code,
            },
        )
        self.assertEqual(status, 201, body)

    def test_password_reset_flow(self) -> None:
        user = self.register()
        self.call("POST", "/api/auth/logout", {})
        code = self.request_code(user["email"], "reset")
        status, body = self.call(
            "POST",
            "/api/auth/password/reset",
            {
                "email": user["email"],
                "code": code,
                "new_password": "new-correct-horse",
            },
        )
        self.assertEqual(status, 200, body)

        self.jar.clear()
        status, _ = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "correct-horse"},
        )
        self.assertEqual(status, 401)
        status, body = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "new-correct-horse"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["user"]["id"], user["id"])

    def test_reset_for_unknown_email_is_generic(self) -> None:
        email = "nobody_" + uuid.uuid4().hex[:8] + "@example.com"
        status, body = self.call(
            "POST",
            "/api/auth/email-code",
            {"email": email, "purpose": "reset"},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["sent"])
        self.assertNotIn((email, "reset"), self.mailer.sent)

    def test_delete_account(self) -> None:
        self.register()
        self.call("PUT", "/api/data", {"data": {"tasks": []}})
        status, _ = self.call(
            "DELETE", "/api/account", {"password": "correct-horse"}
        )
        self.assertEqual(status, 200)
        status, _ = self.call("GET", "/api/auth/me")
        self.assertEqual(status, 401)
        status, _ = self.call(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": "correct-horse"},
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
