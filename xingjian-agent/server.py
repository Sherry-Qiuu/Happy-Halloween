"""Xingjian app service: accounts, user data, and the DeepSeek agent.

Serves the prototype from ``web/``, stores accounts and per-user data in
``XINGJIAN_DATA_DIR``, and proxies the agent panel to DeepSeek. Standard
library only::

    python server.py

Pages
    GET  /                     the prototype

Accounts
    POST /api/auth/register    create an account and start a session
    POST /api/auth/login       sign in
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

import json
import mimetypes
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

import deepseek
import mock_agent
import sched_profile
from accounts import (
    MAX_DATA_BYTES,
    SESSION_COOKIE,
    AccountError,
    AccountStore,
    StorageError,
)
from config import Settings, load_settings
from prompts import chat_messages, report_messages
from protocol import sse

MAX_BODY = 64 * 1024
MAX_AUTH_BODY = 16 * 1024
MAX_DATA_BODY = MAX_DATA_BYTES + 8 * 1024
MAX_MESSAGES = 20
MAX_CONTENT = 2000
MAX_STATE = 60_000
REPORT_MAX_TOKENS = 700

LOGIN_IP_LIMIT = 30
LOGIN_USER_LIMIT = 20
REGISTER_IP_LIMIT = 5
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

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit)
        self.accounts = AccountStore(
            settings.data_dir,
            session_days=settings.session_days,
            max_users=settings.max_users,
        )
        self.login_ip = RateLimiter(LOGIN_IP_LIMIT, AUTH_WINDOW)
        self.login_user = RateLimiter(LOGIN_USER_LIMIT, AUTH_WINDOW)
        self.register_ip = RateLimiter(REGISTER_IP_LIMIT, AUTH_WINDOW)
        self.mock_context = "home"
        super().__init__((settings.host, settings.port), AgentHandler)


class AgentHandler(BaseHTTPRequestHandler):
    server: AgentServer
    server_version = "XingjianAgent/1.0"
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
        routes = {
            "/api/auth/register": self._handle_register,
            "/api/auth/login": self._handle_login,
            "/api/auth/logout": self._handle_logout,
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
                "service": "xingjian",
                "registration": settings.allow_registration,
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
        user = self.server.accounts.create_user(
            payload.get("username"),
            payload.get("password"),
            payload.get("display_name"),
            ip=ip,
        )
        token = self.server.accounts.create_session(user["id"])
        self._send_json(
            HTTPStatus.CREATED,
            {"user": user},
            {"Set-Cookie": self._cookie(token)},
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
        updated_at = self.server.accounts.save_data(
            user["id"], payload["data"]
        )
        self._send_json(
            HTTPStatus.OK, {"updated_at": updated_at}
        )

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
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": self._cookie(None, 0)},
        )

    def _agent_user(self) -> dict[str, Any] | None:
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
        turn = int(profile.get("turn", 0)) + 1
        persona_raw: dict[str, Any] | None = None
        messages = chat_messages(
            history, state, sched_profile.prompt_block(profile, turn)
        )
        self._start_stream()

        producer = mock_agent if settings.mock else deepseek
        try:
            for kind, value in producer.iter_reply(
                settings, messages, goals_count, context=context
            ):
                if kind == "delta":
                    alive = self._write_event("delta", {"text": value})
                elif kind == "proposal":
                    alive = self._write_event("proposal", value)
                elif kind == "persona":
                    # Server-side only: never forwarded to the browser.
                    persona_raw = value if isinstance(value, dict) else None
                    alive = True
                else:
                    alive = True
                if not alive:
                    break
        except deepseek.UpstreamError as error:
            self._write_event("error", {"message": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # noqa: BLE001 - report, never crash
            traceback.print_exc()
            self._write_event(
                "error", {"message": f"服务端异常：{type(error).__name__}"}
            )
        finally:
            self._record_profile(
                user["id"], profile, persona_raw, history, turn
            )
            self._write_event("done", {})
            self._finish_stream()

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


def create_server(settings: Settings) -> AgentServer:
    return AgentServer(settings)


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
    print(
        "[agent] 注册："
        + ("开放" if settings.allow_registration else "关闭")
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
    print(f"[agent] 行间已启动：http://{shown}:{port}")
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
