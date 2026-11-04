"""Morrow app service: accounts, user data, and the DeepSeek agent.

Serves the prototype from ``web/``, stores accounts and per-user data in
``MORROW_DATA_DIR``, and proxies the agent panel to DeepSeek. Standard
library only::

    python server.py

Pages
    GET  /                     the prototype

Accounts
    POST /api/auth/email-code  send a registration or reset code
    POST /api/auth/register    create an account (email code required)
    POST /api/auth/password/reset  set a new password with an email code
    POST /api/auth/login       sign in with username + password
    POST /api/auth/logout      clear the current session
    GET  /api/auth/me          current user or 401
    PATCH /api/account         rename the display name
    POST /api/account/password change the password (other sessions end)
    POST /api/account/logout-all
    DELETE /api/account        delete the account and its data

Data
    GET  /api/data             the signed-in user's document
    PUT  /api/data             replace the signed-in user's document

Agent
    GET  /api/agent/health     connection state for the UI
    POST /api/agent/chat       streaming reply (server-sent events)
    POST /api/agent/report     reflection observation, JSON

Operations
    GET  /api/health           liveness probe for Docker / systemd
"""

from __future__ import annotations

import io
import json
import mimetypes
import sys
import threading
import time
import traceback
import zipfile
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

import conversations
import deepseek
import events
import mock_agent
import policy
import sched_profile
import snapshots
import backend_services
import conversation_memory
import harness
import portrait
from accounts import (
    MAX_DATA_BYTES,
    SESSION_COOKIE,
    AccountError,
    AccountStore,
    StorageError,
    validate_display_name,
    validate_email,
    validate_password,
    validate_username,
)
from config import Settings, load_settings
from mailer import MailError, Mailer
from prompts import chat_messages, observe_messages, report_messages
from protocol import sse
from verification import CodeError, VerificationStore

MAX_BODY = 64 * 1024
MAX_AUTH_BODY = 16 * 1024
MAX_DATA_BODY = MAX_DATA_BYTES + 8 * 1024
MAX_MESSAGES = 20
MAX_CONTENT = 2000
MAX_STATE = 60_000
REPORT_MAX_TOKENS = 700
REPLAY_DELAY = 0.012  # seconds between replayed chunks of a sanitized reply

LOGIN_IP_LIMIT = 30
LOGIN_USER_LIMIT = 20
REGISTER_IP_LIMIT = 5
EMAIL_CODE_IP_LIMIT = 10
RESET_IP_LIMIT = 10
AUTH_WINDOW = 15 * 60

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
MIME_FALLBACK = "application/octet-stream"
NO_STORE = ("text/html", "text/css", "text/javascript")


class BadRequest(Exception):
    """The browser sent something the service will not accept."""


class RateLimiter:
    """Fixed-window limiter, enough to keep one demo from burning a key."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window
        self._hits: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            if len(self._hits) > 2048:
                self._hits = {
                    k: v
                    for k, v in self._hits.items()
                    if now - v[0] < self.window
                }
            return count <= self.limit


class AgentServer(ThreadingHTTPServer):
    """HTTP server carrying the configuration and shared state."""

    daemon_threads = True
    allow_reuse_address = True
    # 浏览器一次会并发拉几十个静态文件；反向代理/隧道也会复用大量连接。
    # socketserver 默认 backlog 只有 5，容易被瞬间打满后表现为「连接被拒绝」
    # （对外看就是 Cloudflare 530）。这里放大到 256。
    request_queue_size = 256

    def __init__(
        self,
        settings: Settings,
        *,
        mailer: Mailer | None = None,
        verifier: VerificationStore | None = None,
    ) -> None:
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit)
        self.accounts = AccountStore(
            settings.data_dir,
            session_days=settings.session_days,
            max_users=settings.max_users,
        )
        self.mailer = mailer or Mailer(settings)
        self.verifier = verifier or VerificationStore(
            ttl_seconds=settings.code_ttl,
            resend_seconds=settings.code_resend,
            max_attempts=settings.code_max_attempts,
            max_per_hour=settings.code_max_per_hour,
        )
        self.login_ip = RateLimiter(LOGIN_IP_LIMIT, AUTH_WINDOW)
        self.login_user = RateLimiter(LOGIN_USER_LIMIT, AUTH_WINDOW)
        self.register_ip = RateLimiter(REGISTER_IP_LIMIT, AUTH_WINDOW)
        self.email_code_ip = RateLimiter(EMAIL_CODE_IP_LIMIT, AUTH_WINDOW)
        self.reset_ip = RateLimiter(RESET_IP_LIMIT, AUTH_WINDOW)
        self.mock_context = "home"
        super().__init__((settings.host, settings.port), AgentHandler)


class AgentHandler(BaseHTTPRequestHandler):
    server: AgentServer
    server_version = "MorrowAgent/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._handle_health()
            return
        if path == "/api/auth/me":
            self._run_api(self._handle_me, unsafe=False)
            return
        if path == "/api/data":
            self._run_api(self._handle_get_data, unsafe=False)
            return
        if path in ("/api/agent/health", "/api/agent/config"):
            self._send_health()
            return
        if path == "/api/agent/history":
            self._handle_history()
            return
        if path == "/api/agent/profile":
            self._handle_profile()
            return
        if path == "/api/account/export":
            self._handle_export()
            return
        if path.startswith("/api/"):
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "unknown endpoint", "code": "not_found"},
            )
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path == "/api/agent/chat":
            self._handle_chat()
            return
        if path == "/api/agent/report":
            self._handle_report()
            return
        if path == "/api/agent/decompose":
            self._handle_structured(backend_services.decompose)
            return
        if path == "/api/agent/replan":
            self._handle_structured(backend_services.replan)
            return
        if path == "/api/agent/reflection":
            self._handle_structured(backend_services.reflection)
            return
        if path == "/api/agent/feedback":
            self._handle_feedback()
            return
        if path == "/api/agent/observe":
            self._handle_observe()
            return
        if path == "/api/agent/event":
            self._handle_event()
            return
        routes = {
            "/api/auth/register": self._handle_register,
            "/api/auth/login": self._handle_login,
            "/api/auth/logout": self._handle_logout,
            "/api/auth/email-code": self._handle_email_code,
            "/api/auth/password/reset": self._handle_reset_password,
            "/api/account/password": self._handle_change_password,
            "/api/account/logout-all": self._handle_logout_all,
        }
        handler = routes.get(path)
        if handler is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "unknown endpoint", "code": "not_found"},
            )
            return
        self._run_api(handler)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path == "/api/data":
            self._run_api(self._handle_put_data)
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "unknown endpoint", "code": "not_found"},
        )

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path == "/api/account":
            self._run_api(self._handle_update_account)
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "unknown endpoint", "code": "not_found"},
        )

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path == "/api/account":
            self._run_api(self._handle_delete_account)
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "unknown endpoint", "code": "not_found"},
        )

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PATCH, DELETE, OPTIONS",
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and self.server.settings.origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if self.server.settings.cookie_secure:
            self.send_header(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._cors_headers()
        self._security_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        *,
        filename: str | None = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if filename:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
        self._cors_headers()
        self._security_headers()
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self, limit: int = MAX_BODY) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            raise BadRequest("Content-Length 无效")
        if length <= 0:
            raise BadRequest("请求体为空")
        if length > limit:
            raise BadRequest("请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("请求体不是合法 JSON")
        if not isinstance(payload, dict):
            raise BadRequest("请求体必须是 JSON 对象")
        return payload

    def _client_ip(self) -> str:
        if self.server.settings.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            return None
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel and morsel.value else None

    def _cookie(self, token: str | None, max_age: int | None = None) -> str:
        settings = self.server.settings
        if max_age is None:
            max_age = settings.session_days * 86_400
        parts = [
            f"{SESSION_COOKIE}={token or ''}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={max_age}",
        ]
        if settings.cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    def _current_user(self) -> dict[str, Any]:
        user = self.server.accounts.resolve_session(self._session_token())
        if user is None:
            raise AccountError(
                "登录已过期，请重新登录。",
                status=401,
                code="unauthenticated",
            )
        return user

    def _origin_ok(self) -> bool:
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            referer = self.headers.get("Referer", "")
            if not referer:
                return True
            parts = urlsplit(referer)
            if not parts.scheme or not parts.netloc:
                return False
            origin = f"{parts.scheme}://{parts.netloc}"
        if origin == "null":
            return False
        parts = urlsplit(origin)
        if not parts.netloc:
            return False
        if parts.netloc == self.headers.get("Host", ""):
            return True
        return self.server.settings.origin_allowed(origin)

    def _require_json_content(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        main = content_type.split(";", 1)[0].strip().lower()
        if main != "application/json":
            raise BadRequest("Content-Type 必须是 application/json")

    def _run_api(self, callback: Any, *, unsafe: bool = True) -> None:
        try:
            if unsafe:
                if not self._origin_ok():
                    raise AccountError(
                        "跨站请求被拒绝。",
                        status=403,
                        code="origin_denied",
                    )
                self._require_json_content()
            callback()
        except BadRequest as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error), "code": "bad_request"},
            )
        except AccountError as error:
            self._send_json(
                error.status,
                {"error": error.message, "code": error.code},
            )
        except StorageError:
            traceback.print_exc()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "服务端存储暂时不可用，请稍后再试。",
                    "code": "storage_error",
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - report, never crash the server
            traceback.print_exc()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "服务端异常，请稍后再试。",
                    "code": "internal",
                },
            )

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _send_health(self) -> None:
        settings = self.server.settings
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "configured": settings.configured,
                "mock": settings.mock,
                "model": settings.model,
                "endpoint": settings.chat_endpoint,
                "provider": "deepseek",
            },
        )

    # ------------------------------------------------------------------
    # Accounts and user data
    # ------------------------------------------------------------------
    def _handle_health(self) -> None:
        settings = self.server.settings
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "service": "morrow",
                "registration": settings.allow_registration,
                "email": settings.mail_configured,
                "email_required": settings.email_required,
                "dev_bypass_auth": settings.dev_bypass_auth,
            },
        )

    def _consume_code(
        self, email: str, purpose: str, code: Any
    ) -> None:
        try:
            self.server.verifier.verify(email, purpose, code)
        except CodeError as error:
            raise AccountError(
                error.message, status=error.status, code=error.code
            )

    def _issue_and_send_code(self, email: str, purpose: str) -> None:
        try:
            code = self.server.verifier.issue(email, purpose)
        except CodeError as error:
            raise AccountError(
                error.message, status=error.status, code=error.code
            )
        try:
            self.server.mailer.send_code(email, code, purpose)
        except MailError as error:
            # A failed delivery must not leave the user in a 60-second
            # cooldown, so the reservation is rolled back.
            self.server.verifier.release(email, purpose)
            raise AccountError(
                error.message, status=error.status, code=error.code
            )

    def _handle_email_code(self) -> None:
        settings = self.server.settings
        if not settings.email_required:
            raise AccountError(
                "邮箱验证已关闭，直接注册即可。",
                code="email_disabled",
            )
        ip = self._client_ip()
        if not self.server.email_code_ip.allow(ip):
            raise AccountError(
                "请求过于频繁，请稍后再试。",
                status=429,
                code="rate_limited",
            )
        payload = self._read_json(MAX_AUTH_BODY)
        purpose = payload.get("purpose")
        if purpose not in ("register", "reset"):
            raise AccountError(
                "验证码用途不正确。", code="purpose_invalid"
            )
        email = validate_email(payload.get("email"))
        base = {
            "ok": True,
            "sent": True,
            "expires_in": settings.code_ttl,
            "resend_in": settings.code_resend,
        }

        if purpose == "register":
            username = payload.get("username")
            if username:
                self.server.accounts.ensure_registration_available(
                    username, email
                )
            elif self.server.accounts.email_exists(email):
                raise AccountError(
                    "这个邮箱已经注册过账号，请直接登录或找回密码。",
                    status=409,
                    code="email_taken",
                )
            self._issue_and_send_code(email, "register")
            self._send_json(
                HTTPStatus.OK,
                {
                    **base,
                    "message": "验证码已发送，请查收邮箱（也看一眼垃圾邮件）。",
                },
            )
            return

        # Password reset must not reveal whether an address is registered:
        # an unknown address gets the same response, but no mail is sent.
        account = self.server.accounts.find_by_email(email)
        if account is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    **base,
                    "message": "如果这个邮箱已注册，验证码会在几分钟内送达。",
                },
            )
            return
        try:
            self._issue_and_send_code(email, "reset")
        except AccountError as error:
            if error.status == 429:
                # Cooldown / hourly cap: answer exactly like a success so
                # the endpoint cannot be used to enumerate accounts.
                self._send_json(
                    HTTPStatus.OK,
                    {
                        **base,
                        "message": "如果这个邮箱已注册，验证码会在几分钟内送达。",
                    },
                )
                return
            raise
        self._send_json(
            HTTPStatus.OK,
            {
                **base,
                "message": "如果这个邮箱已注册，验证码会在几分钟内送达。",
            },
        )

    def _handle_register(self) -> None:
        if not self.server.settings.allow_registration:
            raise AccountError(
                "注册已关闭，请联系管理员。",
                status=403,
                code="registration_closed",
            )
        ip = self._client_ip()
        if not self.server.register_ip.allow(ip):
            raise AccountError(
                "注册过于频繁，请稍后再试。",
                status=429,
                code="rate_limited",
            )
        payload = self._read_json(MAX_AUTH_BODY)
        username = validate_username(payload.get("username"))
        password = validate_password(payload.get("password"))
        display_name = validate_display_name(
            payload.get("display_name"), username
        )
        email_required = self.server.settings.email_required
        if email_required:
            email = validate_email(payload.get("email"))
            self.server.accounts.ensure_registration_available(
                username, email
            )
        else:
            email = None
        if self.server.accounts.user_count >= self.server.settings.max_users:
            raise AccountError(
                "账号数量已达到上限，请联系管理员。",
                status=507,
                code="user_limit",
            )
        if email_required:
            self._consume_code(email, "register", payload.get("code"))
        user = self.server.accounts.create_user(
            username,
            password,
            display_name,
            email=email,
            email_verified=email is not None,
            ip=ip,
        )
        token = self.server.accounts.create_session(user["id"])
        self._send_json(
            HTTPStatus.CREATED,
            {"user": user},
            {"Set-Cookie": self._cookie(token)},
        )

    def _handle_reset_password(self) -> None:
        if not self.server.settings.email_required:
            raise AccountError(
                "邮箱验证已关闭，无法通过邮箱重置密码。",
                code="email_disabled",
            )
        ip = self._client_ip()
        if not self.server.reset_ip.allow(ip):
            raise AccountError(
                "尝试次数过多，请 15 分钟后再试。",
                status=429,
                code="rate_limited",
            )
        payload = self._read_json(MAX_AUTH_BODY)
        email = validate_email(payload.get("email"))
        new_password = validate_password(
            payload.get("new_password"), "新密码"
        )
        self._consume_code(email, "reset", payload.get("code"))
        self.server.accounts.reset_password_by_email(
            email, new_password, ip=ip
        )
        self._send_json(
            HTTPStatus.OK,
            {"ok": True, "message": "密码已重置，请用新密码登录。"},
        )

    def _handle_login(self) -> None:
        ip = self._client_ip()
        payload = self._read_json(MAX_AUTH_BODY)
        raw_username = payload.get("username")
        username_key = (
            raw_username.strip().lower()
            if isinstance(raw_username, str)
            else "?"
        )
        if not self.server.login_ip.allow(ip) or not (
            self.server.login_user.allow(username_key)
        ):
            raise AccountError(
                "尝试次数过多，请 15 分钟后再试。",
                status=429,
                code="rate_limited",
            )
        user = self.server.accounts.authenticate(
            raw_username, payload.get("password"), ip=ip
        )
        token = self.server.accounts.create_session(user["id"])
        self._send_json(
            HTTPStatus.OK,
            {"user": user},
            {"Set-Cookie": self._cookie(token)},
        )

    def _handle_logout(self) -> None:
        self.server.accounts.revoke_session(self._session_token())
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": self._cookie(None, 0)},
        )

    def _handle_me(self) -> None:
        user = self.server.accounts.resolve_session(self._session_token())
        if user is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "请先登录。", "code": "unauthenticated"},
            )
            return
        self._send_json(HTTPStatus.OK, {"user": user})

    def _handle_get_data(self) -> None:
        user = self._current_user()
        data, updated_at = self.server.accounts.load_data(user["id"])
        self._send_json(
            HTTPStatus.OK, {"data": data, "updated_at": updated_at}
        )

    def _handle_put_data(self) -> None:
        user = self._current_user()
        payload = self._read_json(MAX_DATA_BODY)
        if "data" not in payload:
            raise BadRequest("缺少 data")
        previous = self._previous_state(user["id"])
        updated_at = self.server.accounts.save_data(
            user["id"], payload["data"]
        )
        self._archive_state(user["id"], payload["data"], previous)
        self._send_json(
            HTTPStatus.OK, {"updated_at": updated_at}
        )

    def _previous_state(self, user_id: str) -> dict[str, Any] | None:
        """The state the browser is about to overwrite, for the L2 diff."""
        settings = self.server.settings
        if not settings.snapshot_enabled or settings.snapshot_dir is None:
            return None
        try:
            rows = snapshots.read(settings.snapshot_dir, user_id, limit=1)
        except (OSError, ValueError):
            return None
        if not rows:
            return None
        state = rows[-1].get("state")
        return state if isinstance(state, dict) else None

    def _archive_state(
        self,
        user_id: str,
        state: Any,
        previous: dict[str, Any] | None = None,
    ) -> None:
        """L0 snapshot plus the L2 events that the save implies."""
        settings = self.server.settings
        try:
            if settings.snapshot_enabled and settings.snapshot_dir is not None:
                snapshots.append(settings.snapshot_dir, user_id, state)
        except (OSError, ValueError):
            # Archiving must never break the save the user asked for.
            traceback.print_exc()
        if settings.events_dir is None or previous is None:
            return
        try:
            items = events.diff_state(previous, state)
            if items:
                events.append_many(settings.events_dir, user_id, items)
        except (OSError, ValueError):
            traceback.print_exc()

    def _handle_update_account(self) -> None:
        user = self._current_user()
        payload = self._read_json(MAX_AUTH_BODY)
        updated = self.server.accounts.update_display_name(
            user["id"],
            payload.get("display_name"),
            ip=self._client_ip(),
        )
        self._send_json(HTTPStatus.OK, {"user": updated})

    def _handle_change_password(self) -> None:
        user = self._current_user()
        payload = self._read_json(MAX_AUTH_BODY)
        self.server.accounts.change_password(
            user["id"],
            payload.get("current_password"),
            payload.get("new_password"),
            ip=self._client_ip(),
        )
        token = self.server.accounts.create_session(user["id"])
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": self._cookie(token)},
        )

    def _handle_logout_all(self) -> None:
        user = self._current_user()
        self.server.accounts.revoke_all(user["id"])
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": self._cookie(None, 0)},
        )

    def _handle_delete_account(self) -> None:
        user = self._current_user()
        payload = self._read_json(MAX_AUTH_BODY)
        self.server.accounts.delete_user(
            user["id"],
            payload.get("password"),
            ip=self._client_ip(),
        )
        settings = self.server.settings
        if settings.snapshot_dir is not None:
            try:
                snapshots.remove_user(settings.snapshot_dir, user["id"])
            except (OSError, ValueError):
                traceback.print_exc()
        for remover, folder in (
            (events.remove_user, settings.events_dir),
            (conversations.remove_user, settings.conversations_dir),
        ):
            if folder is None:
                continue
            try:
                remover(folder, user["id"])
            except (OSError, ValueError):
                traceback.print_exc()
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": self._cookie(None, 0)},
        )

    def _agent_user(self) -> dict[str, Any] | None:
        if self.server.settings.dev_bypass_auth:
            # 本地演示没有真实账号，但仍要一个合法 id，
            # 这样画像和长期记忆才能落到数据目录里。
            return {"id": "u_0000000000000000", "username": "dev", "display_name": "本地开发"}
        user = self.server.accounts.resolve_session(self._session_token())
        if user is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "请先登录。", "code": "unauthenticated"},
            )
            return None
        return user

    def _handle_chat(self) -> None:
        settings = self.server.settings
        user = self._agent_user()
        if user is None:
            return
        if not self.server.limiter.allow(f"user:{user['id']}"):
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "请求太频繁，请等一分钟再试。"},
            )
            return
        try:
            payload = self._read_json()
            context, history, state = _parse_chat(payload)
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if not settings.configured:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "error": "服务端还没有配置 DEEPSEEK_API_KEY，"
                    "请看 README 的「配置 DeepSeek」一节。"
                },
            )
            return

        goals_count = _goals_count(state)
        profile = self._load_profile(user["id"])
        profile = self._refresh_portrait(user["id"], profile, state)
        memory = self._load_memory(user["id"])
        pending_questions = bool(
            isinstance(memory.get("pending"), dict)
            and memory["pending"].get("questions")
        )
        discuss_rounds = int(memory.get("clarify_rounds") or 0)
        first_text = history[0]["content"] if history else ""
        tool_context = {
            "scheduling_params": sched_profile.scheduling_params(profile)
        }
        discuss_mode = _needs_discussion(
            first_text,
            rounds_done=discuss_rounds,
            has_pending=pending_questions,
        )
        turn = int(profile.get("turn", 0)) + 1
        persona_raw: dict[str, Any] | None = None
        draft_raw: dict[str, Any] | None = None
        proposal: dict[str, Any] | None = None
        clarify_raw: dict[str, Any] | None = None
        parts: list[str] = []
        clean = ""
        user_text = history[-1]["content"] if history else ""
        profile = self._learn_style(user["id"], profile, user_text)
        if discuss_mode:
            messages_discuss = (
                "<discuss>本轮是讨论轮：用户说的还不足以直接排程。"
                "只能输出 clarify 块（最多 3 个问题，每题 2–4 个候选答案），"
                "不要输出 changes，也不要假装已经排好。提问要能直接改变拆解结果。</discuss>"
            )
        elif pending_questions or discuss_rounds >= 2:
            messages_discuss = (
                "<act>用户已经回答了你上一轮的问题，或讨论轮次已用完。"
                "本轮必须输出改动块（changes 非空），不要再提同样的问题；"
                "仍有不确定的地方用最合理的假设并在正文注明估算。</act>"
            )
        else:
            messages_discuss = ""
        messages = chat_messages(
            history,
            state,
            sched_profile.prompt_block(profile, turn),
            conversation_memory.digest(memory),
            portrait.prompt_block(profile.get("derived")),
            messages_discuss,
        )
        self._start_stream()

        prefetched = None
        if not settings.mock:
            try:
                prefetched = harness.fallback_plan(state, user_text, tool_context)
            except Exception:  # noqa: BLE001
                prefetched = None
            hint = "正在核对日程与空档…" if prefetched else "正在理解你的需求…"
            self._write_event("status", {"text": hint})

        try:
            if settings.mock:
                for kind, value in mock_agent.iter_reply(
                    settings, messages, goals_count, context=context
                ):
                    if kind == "delta":
                        parts.append(value)
                    elif kind == "proposal":
                        proposal = value if isinstance(value, dict) else None
                    elif kind == "persona":
                        persona_raw = value if isinstance(value, dict) else None
                    elif kind == "draft":
                        draft_raw = value if isinstance(value, dict) else None
                    elif kind == "clarify":
                        clarify_raw = value if isinstance(value, dict) else None
            else:
                result = harness.run_turn(
                    settings,
                    messages,
                    state,
                    goals_count,
                    write_event=self._write_event,
                    log=lambda line: sys.stdout.write(line + "\n"),
                    context=tool_context,
                    prefetched=prefetched,
                )
                parts.append(result["text"])
                proposal = result["proposal"]
                clarify_raw = result["clarify"]
                persona_raw = result["persona"]
                draft_raw = result["draft"]
                if result["tools"]:
                    sys.stdout.write(
                        "[tool] used " + ",".join(result["tools"]) + "\n"
                    )
                    sys.stdout.flush()
                sys.stdout.write(
                    "[turn] model_calls="
                    + str(result.get("model_calls"))
                    + " tools="
                    + str(len(result["tools"]))
                    + "\n"
                )
                sys.stdout.flush()
        except deepseek.UpstreamError as error:
            self._write_event("error", {"message": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # noqa: BLE001 - report, never crash
            traceback.print_exc()
            self._write_event(
                "error", {"message": f"服务端异常：{type(error).__name__}"}
            )
        else:
            # harness 已经边生成边推了 delta，这里再推一遍就会重复；
            # mock 走原来的 policy 过滤 + 回放。
            clean = "".join(parts)
            if settings.mock:
                clean = self._emit_reply(clean, user_text, draft_raw) or clean
            wants_act = bool(pending_questions or discuss_rounds >= 2)
            if prefetched and not proposal and not clarify_raw:
                proposal = prefetched
            if (
                not proposal
                and not clarify_raw
                and not prefetched
                and (wants_act or _looks_like_change("".join(parts)))
            ):
                proposal, clarify_raw = self._retry_structure(
                    settings, messages, goals_count, want="act"
                )
                if proposal:
                    proposal = harness.enrich(proposal, state, tool_context)
            if not proposal and not clarify_raw and _looks_like_change("".join(parts)):
                # 模型只写了人话没给改动块：让排程引擎兜底算一版
                fallback = harness.fallback_plan(state, user_text, tool_context)
                if fallback:
                    proposal = fallback
                    sys.stdout.write("[fallback] planner plan used\n")
                    sys.stdout.flush()
            if clarify_raw:
                self._write_event("clarify", clarify_raw)
            if proposal:
                self._write_event("proposal", proposal)
        finally:
            if not policy.detect_crisis(user_text):
                self._record_profile(
                    user["id"], profile, persona_raw, history, turn
                )
                reply_text = "".join(parts).strip()
                if user_text and reply_text:
                    memory = conversation_memory.append_turn(
                        memory,
                        user=user_text,
                        assistant=reply_text,
                        context=context,
                    )
                    if proposal:
                        memory = conversation_memory.clear_pending(memory)
                        memory["clarify_rounds"] = 0
                    elif clarify_raw:
                        memory = conversation_memory.set_pending(
                            memory, clarify_raw
                        )
                        memory["clarify_rounds"] = discuss_rounds + 1
                    self._save_memory(user["id"], memory)
            self._capture_turn(
                user["id"], memory, user_text, clean or "".join(parts)
            )
            self._write_event("done", {})
            self._finish_stream()

    def _retry_structure(
        self,
        settings: Settings,
        messages: list[dict[str, str]],
        goals_count: int | None,
        *,
        want: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Ask once more, only for the structured block.

        Some turns come back as prose with no changes and no questions;
        streaming already delivered the text, so this second, silent call
        only fills in the missing block.
        """
        prompt = (
            "只输出结构化块，不要任何正文。如果需要用户先回答，输出 clarify 块；"
            "否则输出 changes 块（至少 1 条）。"
            if want == "act"
            else "只输出 clarify 块，2-3 个问题，每题给候选答案。"
        )
        retry_messages = list(messages) + [
            {"role": "user", "content": prompt},
        ]
        proposal: dict[str, Any] | None = None
        clarify: dict[str, Any] | None = None
        try:
            producer = mock_agent if settings.mock else deepseek
            for kind, value in producer.iter_reply(
                settings, retry_messages, goals_count, context="home"
            ):
                if kind == "proposal" and isinstance(value, dict):
                    proposal = value
                elif kind == "clarify" and isinstance(value, dict):
                    clarify = value
                if proposal or clarify:
                    break
        except Exception:  # noqa: BLE001 - 兜底失败就当没发生
            traceback.print_exc()
        return proposal, clarify

    # ------------------------------------------------------------------
    # Output policy
    # ------------------------------------------------------------------
    def _emit_reply(
        self,
        raw: str,
        user_text: str,
        draft_raw: dict[str, Any] | None,
    ) -> str:
        """Filter the reply, then replay it — only clean text is streamed."""
        crisis = policy.detect_crisis(user_text)
        clean, violations = policy.apply(raw, crisis=crisis)

        if not crisis:
            draft = policy.validate_draft(draft_raw)
            missing = policy.missing_facts(draft, clean)
            if missing:
                clean, added = policy.append_missing(clean, missing)
                violations.append({"rule": "fact_omitted", "facts": added})

        for item in violations:
            sys.stdout.write(
                "[policy] " + json.dumps(item, ensure_ascii=False) + "\n"
            )
        sys.stdout.flush()

        for chunk in policy.chunks(clean):
            if not self._write_event("delta", {"text": chunk}):
                return clean
            time.sleep(REPLAY_DELAY)
        return clean

    def _capture_turn(
        self,
        user_id: str,
        memory: dict[str, Any] | None,
        user_text: str,
        reply: str,
    ) -> None:
        """Store the conversation text, scrubbed (docs/data-policy.md)."""
        settings = self.server.settings
        if not settings.capture_text or settings.conversations_dir is None:
            return
        session = str((memory or {}).get("session") or "chat")[:64]
        try:
            conversations.append(
                settings.conversations_dir,
                user_id,
                session=session,
                role="user",
                text=user_text,
            )
            if reply:
                conversations.append(
                    settings.conversations_dir,
                    user_id,
                    session=session,
                    role="assistant",
                    text=reply,
                )
        except (OSError, ValueError):
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Scheduling profile
    # ------------------------------------------------------------------
    def _load_profile(self, user_id: str) -> dict[str, Any]:
        try:
            profile = self.server.accounts.load_profile(user_id)
        except (AccountError, StorageError):
            traceback.print_exc()
            return sched_profile.empty_profile()
        return profile or sched_profile.empty_profile()

    def _record_profile(
        self,
        user_id: str,
        profile: dict[str, Any],
        raw_delta: dict[str, Any] | None,
        history: list[dict[str, str]],
        turn: int,
    ) -> None:
        """Validate the model's writes, then persist only what survived."""
        if not raw_delta:
            return
        try:
            result = sched_profile.advance(
                profile,
                user_text=history[-1]["content"] if history else "",
                raw_delta=raw_delta,
                turn=turn,
            )
            if result["changed"]:
                self.server.accounts.save_profile(user_id, result["profile"])
            for item in result["rejected"]:
                sys.stdout.write(f"[profile] rejected {json.dumps(item, ensure_ascii=False)}\n")
                sys.stdout.flush()
        except (AccountError, StorageError, sched_profile.ProfileError):
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Long-term memory
    # ------------------------------------------------------------------
    def _load_memory(self, user_id: str) -> dict[str, Any]:
        try:
            stored = self.server.accounts.load_memory(user_id)
        except (AccountError, StorageError):
            traceback.print_exc()
            return conversation_memory.empty_memory()
        return conversation_memory.ensure_memory(stored)

    def _save_memory(self, user_id: str, memory: dict[str, Any]) -> None:
        try:
            self.server.accounts.save_memory(user_id, memory)
        except (AccountError, StorageError):
            traceback.print_exc()

    def _refresh_portrait(
        self,
        user_id: str,
        profile: dict[str, Any],
        state: Any,
    ) -> dict[str, Any]:
        """每次对话顺手量一次行为画像，写回 profile 供下一轮使用。"""
        try:
            derived = portrait.derive(
                state, profile.get("derived"), profile.get("feedback")
            )
        except Exception:  # noqa: BLE001 - 画像失败不能影响对话
            traceback.print_exc()
            return profile
        updated = dict(profile)
        updated["derived"] = derived
        try:
            self.server.accounts.save_profile(user_id, updated)
        except (AccountError, StorageError):
            traceback.print_exc()
        return updated

    def _learn_style(
        self, user_id: str, profile: dict[str, Any], text: str
    ) -> dict[str, Any]:
        """Learn how this user writes — from the user's side only.

        Style is statistics, not a model write, so it takes effect in the
        same turn. A no-op turn must not touch the file: the equality check
        keeps the write count at "only when a counter moved".
        """
        try:
            updated = sched_profile.observe_style(profile, text)
        except (sched_profile.ProfileError, ValueError):
            traceback.print_exc()
            return profile
        if not isinstance(updated, dict) or updated == profile:
            return profile
        try:
            self.server.accounts.save_profile(user_id, updated)
        except (AccountError, StorageError):
            traceback.print_exc()
            return profile
        return updated

    def _handle_observe(self) -> None:
        """Generate one structured observation for a day / week."""
        settings = self.server.settings
        user = self._agent_user()
        if user is None:
            return
        if not self.server.limiter.allow(f"user:{user['id']}"):
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS, {"error": "请求太频繁，请稍后再试。"}
            )
            return
        try:
            payload = self._read_json()
            state = _state(payload)
            reflection = payload.get("reflection")
            conversation = payload.get("conversation")
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        reflection_text = reflection.strip()[:2000] if isinstance(reflection, str) else ""
        chat_text = conversation.strip()[:2000] if isinstance(conversation, str) else ""

        if settings.mock:
            observation = _mock_observation(state, reflection_text)
        else:
            if not settings.configured:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "服务端还没有配置模型 key。"},
                )
                return
            messages = observe_messages(state, reflection_text, chat_text)
            try:
                raw = deepseek.complete(settings, messages)
            except deepseek.UpstreamError as error:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
                return
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"生成失败：{type(error).__name__}"},
                )
                return
            observation = _parse_observation(raw)
            if observation is None:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY, {"error": "模型没有给出可用的观察"}
                )
                return

        observation["generated_at"] = time.time()
        self._send_json(HTTPStatus.OK, {"observation": observation})

    def _handle_feedback(self) -> None:
        """Record what the human actually accepted, for the portrait."""
        user = self._agent_user()
        if user is None:
            return
        try:
            payload = self._read_json()
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        action = str(payload.get("action") or "").strip()
        source = str(payload.get("source") or "ai").strip()
        if action not in ("accept", "dismiss", "choose"):
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "action 只能是 accept/dismiss/choose"}
            )
            return
        summary = str(payload.get("summary") or "")[:120]
        changes = payload.get("changes")
        count = len(changes) if isinstance(changes, list) else 0
        profile = self._load_profile(user["id"])
        feedback = dict(profile.get("feedback") or {})
        counts = dict(feedback.get("counts") or {})
        key = f"{source}:{action}"
        counts[key] = int(counts.get(key, 0)) + 1
        recent = list(feedback.get("recent") or [])
        recent.append(
            {
                "ts": time.time(),
                "action": action,
                "source": source,
                "summary": summary,
                "changes": count,
            }
        )
        feedback["counts"] = counts
        feedback["recent"] = recent[-40:]
        profile["feedback"] = feedback
        try:
            self.server.accounts.save_profile(user["id"], profile)
        except (AccountError, StorageError):
            traceback.print_exc()
        self._record_proposal_event(user["id"], action, source, summary, count)
        self._send_json(HTTPStatus.OK, {"ok": True, "counts": counts})

    def _record_proposal_event(
        self, user_id: str, action: str, source: str, summary: str, count: int
    ) -> None:
        """L1: accepted / rejected / edited, for the modelling side."""
        settings = self.server.settings
        if settings.events_dir is None:
            return
        name = {
            "accept": "proposal.accepted",
            "dismiss": "proposal.rejected",
            "choose": "proposal.edited",
        }.get(action)
        if not name:
            return
        try:
            events.append(
                settings.events_dir,
                user_id,
                name,
                {"source": source, "summary": summary, "changes": count},
            )
        except (OSError, ValueError):
            traceback.print_exc()

    def _handle_event(self) -> None:
        """Frontend hooks for events the state diff cannot see."""
        user = self._agent_user()
        if user is None:
            return
        settings = self.server.settings
        if settings.events_dir is None:
            self._send_json(HTTPStatus.OK, {"ok": True, "recorded": False})
            return
        try:
            payload = self._read_json()
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        name = str(payload.get("name") or "").strip()
        body = payload.get("payload")
        record = events.append(
            settings.events_dir,
            user["id"],
            name,
            body if isinstance(body, dict) else None,
        )
        if record is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "未知或不合格的事件"}
            )
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "recorded": True})

    def _handle_export(self) -> None:
        """Everything stored for this account, as one zip (data policy §2)."""
        user = self._current_user()
        settings = self.server.settings
        data, _ = self.server.accounts.load_data(user["id"])
        profile = self.server.accounts.load_profile(user["id"]) or {}
        memory = self.server.accounts.load_memory(user["id"]) or {}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "data.json",
                json.dumps(data or {}, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "profile.json",
                json.dumps(profile, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "memory.json",
                json.dumps(memory, ensure_ascii=False, indent=2),
            )
            if settings.events_dir is not None:
                archive.writestr(
                    "events.jsonl",
                    "\n".join(
                        events.export_lines(settings.events_dir, user["id"])
                    ),
                )
            if settings.conversations_dir is not None:
                archive.writestr(
                    "conversations.jsonl",
                    "\n".join(
                        conversations.export_lines(
                            settings.conversations_dir, user["id"]
                        )
                    ),
                )
        self._send_bytes(
            buffer.getvalue(),
            "application/zip",
            filename=f"morrow-{user['id']}.zip",
        )

    def _handle_profile(self) -> None:
        user = self._agent_user()
        if user is None:
            return
        profile = self._load_profile(user["id"])
        turn = int(profile.get("turn", 0) or 0)
        self._send_json(
            HTTPStatus.OK,
            {
                "derived": profile.get("derived") or {},
                "slots": profile.get("slots") or {},
                "indicators": profile.get("indicators") or {},
                "expectation": profile.get("expectation"),
                "turn": turn,
                "feedback": profile.get("feedback") or {},
                "params": sched_profile.scheduling_params(profile),
                "prompt": sched_profile.prompt_block(profile, turn),
            },
        )

    def _handle_history(self) -> None:
        user = self._agent_user()
        if user is None:
            return
        memory = self._load_memory(user["id"])
        self._send_json(
            HTTPStatus.OK,
            {
                "sessions": conversation_memory.sessions(memory),
                "turns": len(memory.get("turns") or []),
                "notes": list(memory.get("notes") or []),
            },
        )

    def _handle_structured(self, operation: Any) -> None:
        """Run a deterministic planning/reflection operation and return JSON."""
        if self._agent_user() is None:
            return
        try:
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("请求体必须是 JSON 对象")
            result = operation(payload)
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except (ValueError, TypeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"处理失败：{type(error).__name__}"})
            return
        self._send_json(HTTPStatus.OK, result)

    def _handle_report(self) -> None:
        settings = self.server.settings
        user = self._agent_user()
        if user is None:
            return
        if not self.server.limiter.allow(f"user:{user['id']}"):
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "请求太频繁，请等一分钟再试。"},
            )
            return
        try:
            payload = self._read_json()
            state = _state(payload)
            question = payload.get("reflection")
            if not isinstance(question, str):
                question = ""
            question = question.strip()[:2000]
        except BadRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if not settings.configured:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "服务端还没有配置 DEEPSEEK_API_KEY。"},
            )
            return

        messages = report_messages(state, question)
        try:
            if settings.mock:
                report = _mock_report(question, state)
            else:
                report = deepseek.complete(settings, messages)
        except deepseek.UpstreamError as error:
            self._send_json(
                HTTPStatus.BAD_GATEWAY, {"error": str(error)}
            )
            return
        except Exception as error:  # noqa: BLE001 - report, never crash
            traceback.print_exc()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"生成失败：{type(error).__name__}"},
            )
            return

        if not report:
            self._send_json(
                HTTPStatus.BAD_GATEWAY, {"error": "模型没有返回内容"}
            )
            return
        # The reflection report keeps its paragraphs but obeys the same
        # tone rules: no emoji, no exclamation marks, no bait-and-switch.
        report, violations = policy.apply(report, max_sentences=None)
        for item in violations:
            sys.stdout.write(
                "[policy] " + json.dumps(item, ensure_ascii=False) + "\n"
            )
        self._send_json(HTTPStatus.OK, {"report": report})

    # ------------------------------------------------------------------
    # Server-sent events
    # ------------------------------------------------------------------
    def _start_stream(self) -> None:
        self._broken = False
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._cors_headers()
        self.end_headers()
        self.close_connection = True

    def _write_event(self, event: str, payload: dict[str, Any]) -> bool:
        if getattr(self, "_broken", False):
            return False
        try:
            self.wfile.write(sse(event, payload))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            self._broken = True
            return False

    def _finish_stream(self) -> None:
        try:
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------
    def _serve_static(self, url_path: str) -> None:
        web_dir = self.server.settings.web_dir
        relative = unquote(url_path).lstrip("/") or "index.html"
        if "\x00" in relative:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "bad path"})
            return
        candidate = (web_dir / relative).resolve()
        try:
            candidate.relative_to(web_dir)
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": f"missing file: {relative}"},
            )
            return

        content_type = (
            MIME_TYPES.get(candidate.suffix.lower())
            or mimetypes.guess_type(candidate.name)[0]
            or MIME_FALLBACK
        )
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if content_type.startswith(NO_STORE):
            self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write(
            "[agent] %s %s\n"
            % (self.address_string(), fmt % args)
        )
        sys.stdout.flush()

    def log_error(self, fmt: str, *args: Any) -> None:
        self.log_message(fmt, *args)


VAGUE_VERBS = (
    "准备", "改", "写", "跑", "做实验", "投", "复习", "整理", "规划", "安排", "学", "练",
)
_CONCRETE_RE = None


def _concrete(text: str) -> bool:
    global _CONCRETE_RE
    if _CONCRETE_RE is None:
        import re as _re

        _CONCRETE_RE = _re.compile(
            r"(\d+\s*(分钟|小时|天)|\d+\s*[号日]|\d{1,2}:\d{2}|之前|截止|明天|今天|后天)"
        )
    return bool(_CONCRETE_RE.search(text))


CHANGE_WORDS = ("挪", "移到", "改成", "新增", "删掉", "删除", "安排", "排进", "往后", "提前", "加一个", "占")


def _looks_like_change(text: str) -> bool:
    clean = (text or "").strip()
    if len(clean) < 6:
        return False
    return any(word in clean for word in CHANGE_WORDS)


def _needs_discussion(text: str, *, rounds_done: int = 0, has_pending: bool = False) -> bool:
    """First turn on a vague, multi-item request should be a discussion round."""
    if has_pending:
        return False
    if rounds_done >= 2:
        return False
    clean = (text or "").strip()
    if len(clean) < 8:
        return False
    if not any(word in clean for word in VAGUE_VERBS):
        return False
    if not _concrete(clean):
        return True
    items = sum(clean.count(mark) for mark in ("，", "。", "；", ",", ";"))
    return items >= 3


def _state(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    if not isinstance(state, dict):
        raise BadRequest("缺少 state")
    try:
        encoded = json.dumps(state, ensure_ascii=False)
    except (TypeError, ValueError):
        raise BadRequest("state 不是合法 JSON")
    if len(encoded) > MAX_STATE:
        raise BadRequest("state 过大")
    return state


def _parse_chat(
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    context = payload.get("context", "home")
    if context not in ("home", "project", "review"):
        raise BadRequest("context 无效")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise BadRequest("messages 不能为空")
    history: list[dict[str, str]] = []
    for item in raw_messages[-MAX_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        history.append({"role": role, "content": text[:MAX_CONTENT]})
    if not history or history[-1]["role"] != "user":
        raise BadRequest("最后一条消息必须是用户消息")
    return context, history, _state(payload)


def _goals_count(state: dict[str, Any]) -> int | None:
    goals = state.get("goals")
    if isinstance(goals, list):
        return min(len(goals), 20)
    return None


def _parse_observation(raw: str) -> dict[str, Any] | None:
    """Pull the JSON object out of the model's observation reply."""
    body = (raw or "").strip()
    if not body:
        return None
    if body.startswith("```"):
        body = body.strip("`")
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    def clean_list(value: Any, limit: int = 3) -> list[str]:
        if not isinstance(value, list):
            return []
        out = []
        for item in value[:limit]:
            if isinstance(item, str) and item.strip():
                out.append(" ".join(item.split())[:160])
        return out

    observation = {
        "facts": clean_list(payload.get("facts")),
        "patterns": clean_list(payload.get("patterns"), 2),
        "experiment": " ".join(str(payload.get("experiment") or "").split())[:160],
        "questions": clean_list(payload.get("questions"), 1),
    }
    if not observation["facts"] and not observation["patterns"]:
        return None
    return observation


def _mock_observation(state: dict[str, Any], reflection: str) -> dict[str, Any]:
    scheduled = state.get("scheduled") if isinstance(state.get("scheduled"), list) else []
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    done = sum(1 for task in tasks if isinstance(task, dict) and task.get("done"))
    minutes = sum(int(row.get("duration") or 0) for row in scheduled if isinstance(row, dict))
    return {
        "facts": [
            f"当天有 {len(scheduled)} 个时间块，合计 {minutes} 分钟（估算）。",
            f"待办 {len(tasks)} 条，完成 {done} 条。",
        ],
        "patterns": ["记录还不多，暂时看不出稳定模式。"],
        "experiment": "明天先给最重要的一件事留一个 25 分钟的起步块。",
        "questions": ["今天哪一段投入最值得保留？"],
        "source": "mock",
    }


def _mock_report(question: str, state: dict[str, Any]) -> str:
    tasks = state.get("tasks") or []
    done = sum(
        1 for task in tasks if isinstance(task, dict) and task.get("done")
    )
    head = "记录到的事实\n"
    body = (
        f"今天排了 {len(tasks)} 项待办，完成了 {done} 项。"
        if tasks
        else "今天没有排待办。"
    )
    quote = (
        f"你写到「{question.strip()[:40]}」。"
        if question.strip()
        else "这次没有写反思正文。"
    )
    return (
        f"{head}{body}{quote}\n\n"
        "可能的模式\n"
        "计划被临时事项切开之后，剩下的时间足够做完最小的一步，"
        "但不足以按原计划推进（估算）。\n\n"
        "可以带走的判断\n"
        "下一步要保留哪一件事，只有你能决定。"
        "这一周里，哪一件做完之后你会觉得没白过？"
    )


def create_server(
    settings: Settings,
    *,
    mailer: Mailer | None = None,
    verifier: VerificationStore | None = None,
) -> AgentServer:
    return AgentServer(settings, mailer=mailer, verifier=verifier)


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:  # pragma: no cover - older interpreters
        pass
    settings = load_settings()
    if settings.mock:
        print("[agent] AGENT_MOCK=1，使用离线示例回复")
    elif not settings.api_key:
        print("[agent] 未配置 DEEPSEEK_API_KEY，AI 面板会提示离线")
    print(f"[agent] 数据目录：{settings.data_dir}")
    if settings.snapshot_enabled and settings.snapshot_dir is not None:
        try:
            pruned = snapshots.prune(
                settings.snapshot_dir, retention_days=settings.snapshot_days
            )
        except OSError:
            pruned = 0
        print(
            f"[agent] 快照归档：{settings.snapshot_dir}"
            f"（保留 {settings.snapshot_days} 天，启动清理 {pruned} 个文件）"
        )
    if settings.events_dir is not None:
        try:
            gone = events.prune(
                settings.events_dir, retention_days=settings.collect_days
            )
        except OSError:
            gone = 0
        print(f"[agent] 事件日志：{settings.events_dir}（保留 {settings.collect_days} 天，清理 {gone}）")
    if settings.capture_text and settings.conversations_dir is not None:
        try:
            gone = conversations.prune(
                settings.conversations_dir,
                retention_days=settings.collect_days,
            )
        except OSError:
            gone = 0
        print(
            f"[agent] 对话原文：{settings.conversations_dir}"
            f"（保留 {settings.collect_days} 天，清理 {gone}）"
        )
    print(
        "[agent] 注册："
        + ("开放" if settings.allow_registration else "关闭")
    )
    if settings.mail_configured:
        where = (
            "控制台（MAIL_TRANSPORT=console）"
            if settings.mail_transport == "console"
            else f"{settings.mail_host}:{settings.mail_port}"
        )
        print(f"[agent] 邮箱验证码：已配置 · {where}")
    else:
        print(
            "[agent] 邮箱验证码：未配置 SMTP_PASSWORD，"
            "注册和找回密码会提示邮箱服务不可用"
        )
    index = settings.web_dir / "index.html"
    if not index.is_file():
        print(f"[agent] 找不到前端文件：{index}")
        return 1
    try:
        httpd = create_server(settings)
    except StorageError as error:
        print(f"[agent] 数据目录不可用：{error}")
        return 1
    except OSError as error:
        if error.errno in (10048, 98):
            print(
                f"[agent] 端口 {settings.port} 已被占用，"
                "换一个：$env:PORT=8001; python server.py"
            )
            return 1
        raise
    host, port = httpd.server_address[:2]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"[agent] Morrow 已启动：http://{shown}:{port}")
    print(f"[agent] 模型：{settings.model} · {settings.chat_endpoint}")
    print("[agent] Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[agent] 已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
