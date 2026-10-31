"""Accounts, sessions, and per-user data for the Xingjian app.

Everything lives in one data directory (``server information`` during
local development, ``/data`` in Docker)::

    accounts.json       user records with PBKDF2 password hashes
    sessions.json       SHA-256 hashes of active session tokens
    data/<user_id>.json one JSON document per user
    audit.log           append-only JSONL account events

The store is intentionally small and dependency-free. It assumes a single
server process (or at least a single writer) and serializes every mutation
with a re-entrant lock. Writes go through a temporary file and
``os.replace`` so a crash cannot leave a half-written JSON file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
SESSION_BYTES = 32
MAX_PASSWORD = 128
MAX_DISPLAY = 40
MAX_DATA_BYTES = 512 * 1024
MAX_PROFILE_BYTES = 64 * 1024
AUDIT_MAX_BYTES = 2 * 1024 * 1024
SESSION_COOKIE = "xj_session"

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
USER_ID_RE = re.compile(r"^u_[0-9a-f]{16}$")
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
MAX_EMAIL = 254

_dummy_hash: str | None = None


class AccountError(Exception):
    """A request the account layer refuses, with an HTTP status."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class StorageError(Exception):
    """The data directory cannot be read or written safely."""


def _now() -> float:
    return time.time()


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(
        timespec="seconds"
    )


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, salt.hex(), digest.hex()
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, rounds
    )
    return hmac.compare_digest(actual, expected)


def _dummy_verify(password: str) -> None:
    """Burn the same CPU time when a username does not exist."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hash_password("timing-equalizer")
    _verify_password(password, _dummy_hash)


def validate_username(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountError("请输入用户名。", code="username_invalid")
    username = value.strip().lower()
    if not USERNAME_RE.match(username):
        raise AccountError(
            "用户名需为 3–32 位，只能包含字母、数字、下划线、点或短横线，"
            "并以字母或数字开头。",
            code="username_invalid",
        )
    return username


def validate_email(value: Any) -> str:
    """Normalize an email address used for verification and recovery."""
    if not isinstance(value, str):
        raise AccountError("请输入邮箱地址。", code="email_invalid")
    email = value.strip().lower()
    if not email:
        raise AccountError("请输入邮箱地址。", code="email_invalid")
    if len(email) > MAX_EMAIL:
        raise AccountError("邮箱地址过长。", code="email_invalid")
    local, _, domain = email.partition("@")
    if (
        not EMAIL_RE.match(email)
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or ".." in domain
    ):
        raise AccountError(
            "邮箱格式不正确，请输入例如 name@example.com。",
            code="email_invalid",
        )
    return email


def validate_password(value: Any, label: str = "密码") -> str:
    if not isinstance(value, str):
        raise AccountError(f"{label}格式不正确。", code="password_invalid")
    if len(value) < 8:
        raise AccountError(f"{label}至少需要 8 个字符。", code="password_weak")
    if len(value) > MAX_PASSWORD:
        raise AccountError(
            f"{label}不能超过 {MAX_PASSWORD} 个字符。", code="password_long"
        )
    if not value.strip():
        raise AccountError(f"{label}不能全是空白。", code="password_weak")
    return value


def validate_display_name(value: Any, username: str) -> str:
    if value is None or value == "":
        return username
    if not isinstance(value, str):
        raise AccountError("显示名称格式不正确。", code="display_invalid")
    name = " ".join(value.split())
    if not name:
        return username
    if len(name) > MAX_DISPLAY:
        raise AccountError(
            f"显示名称不能超过 {MAX_DISPLAY} 个字符。",
            code="display_invalid",
        )
    return name


def _public_user(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "username": record["username"],
        "display_name": record["display_name"],
        "email": record.get("email") or "",
        "email_verified": bool(record.get("email_verified")),
        "created_at": _iso(record.get("created_at")),
        "updated_at": _iso(record.get("updated_at")),
    }


class AccountStore:
    """File-backed account, session, and per-user data store."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        session_days: int = 7,
        max_users: int = 5_000,
    ) -> None:
        self.root = Path(data_dir)
        self.session_seconds = max(1, min(int(session_days), 365)) * 86_400
        self.max_users = max(1, int(max_users))
        self.accounts_path = self.root / "accounts.json"
        self.sessions_path = self.root / "sessions.json"
        self.data_dir = self.root / "data"
        self.audit_path = self.root / "audit.log"
        self._lock = threading.RLock()
        self._accounts: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._ensure_dirs()
        self._load()

    # ------------------------------------------------------------------
    # Loading and persistence
    # ------------------------------------------------------------------
    def _ensure_dirs(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.root, 0o700)
                os.chmod(self.data_dir, 0o700)
        except OSError as error:
            raise StorageError(
                f"无法创建数据目录 {self.root}: {error}"
            ) from error

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StorageError(f"无法读取 {path}: {error}") from error
        if not isinstance(payload, dict):
            raise StorageError(f"{path} 必须是 JSON 对象")
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with open(
                temporary, "w", encoding="utf-8", newline="\n"
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(f"无法写入 {path}: {error}") from error

    def _load(self) -> None:
        accounts = self._read_json(self.accounts_path)
        sessions = self._read_json(self.sessions_path)
        users = accounts.get("users", [])
        records = sessions.get("sessions", [])
        if not isinstance(users, list) or not isinstance(records, list):
            raise StorageError("accounts.json 或 sessions.json 结构不正确")
        self._accounts = [row for row in users if isinstance(row, dict)]
        self._sessions = [row for row in records if isinstance(row, dict)]
        self._prune_sessions(save=False)

    def _save_accounts(self) -> None:
        self._write_json(
            self.accounts_path, {"version": 1, "users": self._accounts}
        )

    def _save_sessions(self) -> None:
        self._write_json(
            self.sessions_path, {"version": 1, "sessions": self._sessions}
        )

    def _prune_sessions(self, save: bool = True) -> None:
        now = _now()
        kept = [
            row for row in self._sessions if row.get("expires_at", 0) > now
        ]
        changed = len(kept) != len(self._sessions)
        self._sessions = kept
        if changed and save:
            self._save_sessions()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    def _audit(
        self,
        event: str,
        *,
        user_id: str = "",
        username: str = "",
        ip: str = "",
    ) -> None:
        record = {
            "at": _iso(_now()),
            "event": event,
            "user_id": user_id,
            "username": username,
            "ip": ip,
        }
        try:
            if (
                self.audit_path.exists()
                and self.audit_path.stat().st_size > AUDIT_MAX_BYTES
            ):
                os.replace(
                    self.audit_path,
                    self.audit_path.with_suffix(".log.1"),
                )
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            if os.name != "nt":
                os.chmod(self.audit_path, 0o600)
        except OSError:
            # An audit failure must never block login or a data save.
            pass

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _find_by_username(self, username: str) -> dict[str, Any] | None:
        for record in self._accounts:
            if record.get("username") == username:
                return record
        return None

    def _find_by_id(self, user_id: str) -> dict[str, Any] | None:
        for record in self._accounts:
            if record.get("id") == user_id:
                return record
        return None

    def _find_by_email(self, email: str) -> dict[str, Any] | None:
        for record in self._accounts:
            if record.get("email") == email:
                return record
        return None

    def find_by_email(self, email: Any) -> dict[str, Any] | None:
        """Return the public user owning *email*, if any."""
        normalized = validate_email(email)
        with self._lock:
            record = self._find_by_email(normalized)
            return _public_user(record) if record else None

    def email_exists(self, email: Any) -> bool:
        normalized = validate_email(email)
        with self._lock:
            return self._find_by_email(normalized) is not None

    def ensure_registration_available(
        self, username: Any, email: Any
    ) -> tuple[str, str]:
        """Validate a username/email pair before an email code is sent.

        Doing this check twice (before sending and inside
        :meth:`create_user`) keeps a taken name from consuming a code
        while still making the final write atomic.
        """
        username = validate_username(username)
        email = validate_email(email)
        with self._lock:
            if self._find_by_username(username) is not None:
                raise AccountError(
                    "这个用户名已经被注册。",
                    status=409,
                    code="username_taken",
                )
            if self._find_by_email(email) is not None:
                raise AccountError(
                    "这个邮箱已经注册过账号，请直接登录或找回密码。",
                    status=409,
                    code="email_taken",
                )
        return username, email

    @property
    def user_count(self) -> int:
        with self._lock:
            return len(self._accounts)

    def create_user(
        self,
        username: Any,
        password: Any,
        display_name: Any = None,
        *,
        email: Any = None,
        email_verified: bool = False,
        ip: str = "",
    ) -> dict[str, Any]:
        username = validate_username(username)
        password = validate_password(password)
        name = validate_display_name(display_name, username)
        normalized_email = validate_email(email) if email is not None else None
        with self._lock:
            if self._find_by_username(username) is not None:
                raise AccountError(
                    "这个用户名已经被注册。",
                    status=409,
                    code="username_taken",
                )
            if (
                normalized_email is not None
                and self._find_by_email(normalized_email) is not None
            ):
                raise AccountError(
                    "这个邮箱已经注册过账号，请直接登录或找回密码。",
                    status=409,
                    code="email_taken",
                )
            if len(self._accounts) >= self.max_users:
                raise AccountError(
                    "账号数量已达到上限，请联系管理员。",
                    status=507,
                    code="user_limit",
                )
            now = _now()
            record = {
                "id": f"u_{secrets.token_hex(8)}",
                "username": username,
                "display_name": name,
                "password_hash": _hash_password(password),
                "session_version": 1,
                "created_at": now,
                "updated_at": now,
            }
            if normalized_email is not None:
                record["email"] = normalized_email
                record["email_verified"] = bool(email_verified)
            self._accounts.append(record)
            self._save_accounts()
            self._audit(
                "register",
                user_id=record["id"],
                username=username,
                ip=ip,
            )
            return _public_user(record)

    def authenticate(
        self, username: Any, password: Any, *, ip: str = ""
    ) -> dict[str, Any]:
        if not isinstance(username, str) or not isinstance(password, str):
            raise AccountError(
                "用户名或密码不正确。",
                status=401,
                code="bad_credentials",
            )
        normalized = username.strip().lower()
        with self._lock:
            record = self._find_by_username(normalized)
            if record is None:
                _dummy_verify(password)
                self._audit(
                    "login_failed", username=normalized, ip=ip
                )
                raise AccountError(
                    "用户名或密码不正确。",
                    status=401,
                    code="bad_credentials",
                )
            if not _verify_password(password, record["password_hash"]):
                self._audit(
                    "login_failed",
                    user_id=record["id"],
                    username=normalized,
                    ip=ip,
                )
                raise AccountError(
                    "用户名或密码不正确。",
                    status=401,
                    code="bad_credentials",
                )
            self._audit(
                "login",
                user_id=record["id"],
                username=normalized,
                ip=ip,
            )
            return _public_user(record)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._find_by_id(user_id)
            return _public_user(record) if record else None

    def update_display_name(
        self, user_id: str, display_name: Any, *, ip: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            record = self._find_by_id(user_id)
            if record is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            record["display_name"] = validate_display_name(
                display_name, record["username"]
            )
            record["updated_at"] = _now()
            self._save_accounts()
            self._audit(
                "profile_updated",
                user_id=user_id,
                username=record["username"],
                ip=ip,
            )
            return _public_user(record)

    def change_password(
        self,
        user_id: str,
        current_password: Any,
        new_password: Any,
        *,
        ip: str = "",
    ) -> None:
        if not isinstance(current_password, str):
            raise AccountError("请输入当前密码。", code="password_invalid")
        new_password = validate_password(new_password, "新密码")
        with self._lock:
            record = self._find_by_id(user_id)
            if record is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            if not _verify_password(
                current_password, record["password_hash"]
            ):
                raise AccountError(
                    "当前密码不正确。",
                    status=403,
                    code="bad_password",
                )
            if current_password == new_password:
                raise AccountError(
                    "新密码不能与当前密码相同。", code="password_same"
                )
            record["password_hash"] = _hash_password(new_password)
            record["session_version"] = int(
                record.get("session_version", 1)
            ) + 1
            record["updated_at"] = _now()
            self._save_accounts()
            self._revoke_all_locked(user_id)
            self._audit(
                "password_changed",
                user_id=user_id,
                username=record["username"],
                ip=ip,
            )

    def reset_password_by_email(
        self, email: Any, new_password: Any, *, ip: str = ""
    ) -> None:
        """Set a new password after the email code has been verified."""
        normalized = validate_email(email)
        new_password = validate_password(new_password, "新密码")
        with self._lock:
            record = self._find_by_email(normalized)
            if record is None:
                raise AccountError(
                    "这个邮箱没有对应的账号。",
                    status=404,
                    code="email_unknown",
                )
            record["password_hash"] = _hash_password(new_password)
            record["session_version"] = int(
                record.get("session_version", 1)
            ) + 1
            record["updated_at"] = _now()
            self._save_accounts()
            self._revoke_all_locked(record["id"])
            self._audit(
                "password_reset",
                user_id=record["id"],
                username=record["username"],
                ip=ip,
            )

    def delete_user(
        self, user_id: str, password: Any, *, ip: str = ""
    ) -> None:
        if not isinstance(password, str):
            raise AccountError("请输入密码以确认删除。", code="password_invalid")
        with self._lock:
            record = self._find_by_id(user_id)
            if record is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            if not _verify_password(password, record["password_hash"]):
                raise AccountError(
                    "密码不正确，账号未删除。",
                    status=403,
                    code="bad_password",
                )
            self._accounts = [
                row for row in self._accounts if row["id"] != user_id
            ]
            self._save_accounts()
            self._revoke_all_locked(user_id)
            try:
                self._data_path(user_id).unlink(missing_ok=True)
                self._profile_path(user_id).unlink(missing_ok=True)
            except OSError as error:
                raise StorageError(
                    f"账号已删除，但数据文件未能删除: {error}"
                ) from error
            self._audit(
                "account_deleted",
                user_id=user_id,
                username=record["username"],
                ip=ip,
            )

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(SESSION_BYTES)
        digest = self._token_digest(token)
        with self._lock:
            record = self._find_by_id(user_id)
            if record is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            now = _now()
            self._sessions.append(
                {
                    "token_hash": digest,
                    "user_id": user_id,
                    "session_version": int(
                        record.get("session_version", 1)
                    ),
                    "created_at": now,
                    "expires_at": now + self.session_seconds,
                }
            )
            self._prune_sessions(save=False)
            self._save_sessions()
        return token

    def resolve_session(self, token: str | None) -> dict[str, Any] | None:
        if not token or not isinstance(token, str):
            return None
        digest = self._token_digest(token)
        with self._lock:
            now = _now()
            for row in self._sessions:
                if row.get("token_hash") != digest:
                    continue
                if row.get("expires_at", 0) <= now:
                    self._sessions.remove(row)
                    self._save_sessions()
                    return None
                record = self._find_by_id(row.get("user_id", ""))
                if record is None:
                    self._sessions.remove(row)
                    self._save_sessions()
                    return None
                if int(row.get("session_version", 0)) != int(
                    record.get("session_version", 1)
                ):
                    self._sessions.remove(row)
                    self._save_sessions()
                    return None
                return _public_user(record)
            return None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        digest = self._token_digest(token)
        with self._lock:
            kept = [
                row
                for row in self._sessions
                if row.get("token_hash") != digest
            ]
            if len(kept) != len(self._sessions):
                self._sessions = kept
                self._save_sessions()

    def _revoke_all_locked(self, user_id: str) -> None:
        kept = [
            row for row in self._sessions if row.get("user_id") != user_id
        ]
        if len(kept) != len(self._sessions):
            self._sessions = kept
            self._save_sessions()

    def revoke_all(self, user_id: str) -> None:
        with self._lock:
            self._revoke_all_locked(user_id)

    # ------------------------------------------------------------------
    # Per-user data
    # ------------------------------------------------------------------
    def _data_path(self, user_id: str) -> Path:
        if not USER_ID_RE.match(user_id or ""):
            raise AccountError(
                "用户标识不正确。", status=400, code="bad_user_id"
            )
        return self.data_dir / f"{user_id}.json"

    def _profile_path(self, user_id: str) -> Path:
        """The scheduling profile lives beside the app data, not inside.

        The browser owns ``data``; the server owns the profile. Keeping
        them apart means a client save can never overwrite what the agent
        learned, and an account purge has one obvious file to remove.
        """
        if not USER_ID_RE.match(user_id or ""):
            raise AccountError(
                "用户标识不正确。", status=400, code="bad_user_id"
            )
        return self.data_dir / f"{user_id}.profile.json"

    def load_profile(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._find_by_id(user_id) is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            payload = self._read_json(self._profile_path(user_id))
            profile = payload.get("profile")
            return profile if isinstance(profile, dict) else None

    def save_profile(self, user_id: str, profile: dict[str, Any]) -> str:
        if not isinstance(profile, dict):
            raise AccountError("画像必须是 JSON 对象。", code="profile_invalid")
        try:
            encoded = json.dumps(profile, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise AccountError(
                f"画像不是合法 JSON: {error}", code="profile_invalid"
            ) from error
        if len(encoded.encode("utf-8")) > MAX_PROFILE_BYTES:
            raise AccountError(
                "画像超过 64 KB，暂时无法保存。",
                status=413,
                code="profile_too_large",
            )
        updated_at = _now()
        with self._lock:
            if self._find_by_id(user_id) is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            self._write_json(
                self._profile_path(user_id),
                {
                    "version": 1,
                    "updated_at": updated_at,
                    "profile": profile,
                },
            )
            return _iso(updated_at) or ""

    def delete_profile(self, user_id: str) -> None:
        with self._lock:
            try:
                self._profile_path(user_id).unlink(missing_ok=True)
            except OSError:
                pass

    def load_data(
        self, user_id: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        with self._lock:
            if self._find_by_id(user_id) is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            path = self._data_path(user_id)
            payload = self._read_json(path)
            data = payload.get("data")
            if not isinstance(data, dict):
                return None, None
            return data, _iso(payload.get("updated_at"))

    def save_data(self, user_id: str, data: Any) -> str:
        if not isinstance(data, dict):
            raise AccountError(
                "数据必须是 JSON 对象。", code="data_invalid"
            )
        try:
            encoded = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise AccountError(
                f"数据不是合法 JSON: {error}", code="data_invalid"
            ) from error
        if len(encoded.encode("utf-8")) > MAX_DATA_BYTES:
            raise AccountError(
                "数据超过 512 KB，暂时无法保存。",
                status=413,
                code="data_too_large",
            )
        updated_at = _now()
        with self._lock:
            if self._find_by_id(user_id) is None:
                raise AccountError(
                    "账号不存在。", status=401, code="unknown_user"
                )
            self._write_json(
                self._data_path(user_id),
                {
                    "version": 1,
                    "updated_at": updated_at,
                    "data": data,
                },
            )
            return _iso(updated_at) or ""

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    def purge_expired_sessions(self) -> int:
        with self._lock:
            before = len(self._sessions)
            self._prune_sessions(save=True)
            return before - len(self._sessions)
