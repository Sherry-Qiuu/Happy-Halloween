"""Short-lived, single-use email verification codes.

Codes are kept in memory on purpose: they expire in ten minutes, are
hashed at rest, and a server restart simply invalidates every pending
code (the user asks for a new one). They are never written to the data
directory or returned by an API.

One code is bound to one (email, purpose) pair, so a registration code
cannot be replayed on the password-reset endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Callable

PURPOSES = ("register", "reset")
CODE_PATTERN = re.compile(r"^\d{6}$")
SEND_WINDOW = 3_600.0


class CodeError(Exception):
    """A code that cannot be issued or accepted right now."""

    def __init__(
        self,
        message: str,
        status: int = 400,
        code: str = "code_invalid",
        retry_after: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after


class VerificationStore:
    """Issue, limit, and verify six-digit email codes."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        resend_seconds: int = 60,
        max_attempts: int = 5,
        max_per_hour: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.resend_seconds = max(10, int(resend_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.max_per_hour = max(1, int(max_per_hour))
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._codes: dict[tuple[str, str], dict[str, object]] = {}
        self._sends: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------
    def issue(self, email: str, purpose: str) -> str:
        """Return a fresh code, or raise :class:`CodeError` on cooldown."""
        if purpose not in PURPOSES:
            raise ValueError(f"unknown verification purpose: {purpose}")
        key_email = self._normalize(email)
        now = self._clock()
        with self._lock:
            self._prune(now)
            current = self._codes.get((key_email, purpose))
            if current is not None:
                wait = int(
                    float(current["sent_at"]) + self.resend_seconds - now
                )
                if wait > 0:
                    raise CodeError(
                        f"验证码发送过于频繁，请 {wait} 秒后再试。",
                        status=429,
                        code="code_too_soon",
                        retry_after=wait,
                    )

            sends = [
                stamp
                for stamp in self._sends.get(key_email, [])
                if now - stamp < SEND_WINDOW
            ]
            if len(sends) >= self.max_per_hour:
                raise CodeError(
                    "这个邮箱获取验证码的次数过多，请一小时后再试。",
                    status=429,
                    code="code_rate_limited",
                )

            code = f"{secrets.randbelow(1_000_000):06d}"
            self._codes[(key_email, purpose)] = {
                "digest": self._digest(key_email, purpose, code),
                "expires_at": now + self.ttl_seconds,
                "attempts": 0,
                "sent_at": now,
            }
            sends.append(now)
            self._sends[key_email] = sends
        return code

    def release(self, email: str, purpose: str) -> None:
        """Forget a just-issued code whose email could not be delivered."""
        key_email = self._normalize(email)
        with self._lock:
            self._codes.pop((key_email, purpose), None)
            sends = self._sends.get(key_email)
            if sends:
                sends.pop()
                if not sends:
                    self._sends.pop(key_email, None)

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------
    def verify(self, email: str, purpose: str, code: str) -> None:
        """Consume *code* when it matches, otherwise raise CodeError."""
        if purpose not in PURPOSES:
            raise ValueError(f"unknown verification purpose: {purpose}")
        key_email = self._normalize(email)
        candidate = code.strip() if isinstance(code, str) else ""
        if not CODE_PATTERN.match(candidate):
            raise CodeError(
                "请输入 6 位数字验证码。", code="code_invalid"
            )

        now = self._clock()
        key = (key_email, purpose)
        with self._lock:
            record = self._codes.get(key)
            if record is None:
                raise CodeError(
                    "验证码已过期，请重新获取。", code="code_expired"
                )
            if now > float(record["expires_at"]):
                self._codes.pop(key, None)
                raise CodeError(
                    "验证码已过期，请重新获取。", code="code_expired"
                )
            attempts = int(record["attempts"])
            if attempts >= self.max_attempts:
                self._codes.pop(key, None)
                raise CodeError(
                    "验证码错误次数过多，请重新获取。",
                    status=429,
                    code="code_attempts",
                )
            expected = str(record["digest"])
            actual = self._digest(key_email, purpose, candidate)
            if not hmac.compare_digest(actual, expected):
                attempts += 1
                record["attempts"] = attempts
                remaining = self.max_attempts - attempts
                if remaining <= 0:
                    self._codes.pop(key, None)
                    raise CodeError(
                        "验证码错误次数过多，请重新获取。",
                        status=429,
                        code="code_attempts",
                    )
                raise CodeError(
                    f"验证码不正确，还可以再试 {remaining} 次。",
                    code="code_mismatch",
                )
            self._codes.pop(key, None)

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    def purge(self) -> None:
        with self._lock:
            self._prune(self._clock())

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, record in self._codes.items()
            if now > float(record["expires_at"])
        ]
        for key in expired:
            self._codes.pop(key, None)
        for email, stamps in list(self._sends.items()):
            kept = [stamp for stamp in stamps if now - stamp < SEND_WINDOW]
            if kept:
                self._sends[email] = kept
            else:
                self._sends.pop(email, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(email: str) -> str:
        return str(email or "").strip().lower()

    def _digest(self, email: str, purpose: str, code: str) -> str:
        payload = f"{email}\x00{purpose}\x00{code}".encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
