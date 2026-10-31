"""Tests for verification codes and the email transport configuration."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings  # noqa: E402
from mailer import Mailer, MailError  # noqa: E402
from verification import CodeError, VerificationStore  # noqa: E402


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class VerificationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.store = VerificationStore(
            ttl_seconds=600,
            resend_seconds=60,
            max_attempts=3,
            max_per_hour=5,
            clock=self.clock,
        )

    def test_code_is_six_digits_and_single_use(self) -> None:
        code = self.store.issue("alice@example.com", "register")
        self.assertRegex(code, r"^\d{6}$")
        self.store.verify("ALICE@example.com", "register", code)
        with self.assertRaises(CodeError) as reused:
            self.store.verify("alice@example.com", "register", code)
        self.assertEqual(reused.exception.code, "code_expired")

    def test_code_is_bound_to_its_purpose(self) -> None:
        code = self.store.issue("alice@example.com", "register")
        with self.assertRaises(CodeError) as wrong_purpose:
            self.store.verify("alice@example.com", "reset", code)
        self.assertEqual(wrong_purpose.exception.code, "code_expired")
        self.store.verify("alice@example.com", "register", code)

    def test_expired_code_is_rejected(self) -> None:
        code = self.store.issue("alice@example.com", "reset")
        self.clock.advance(601)
        with self.assertRaises(CodeError) as expired:
            self.store.verify("alice@example.com", "reset", code)
        self.assertEqual(expired.exception.code, "code_expired")

    def test_wrong_attempts_are_limited(self) -> None:
        code = self.store.issue("alice@example.com", "register")
        wrong = "000000" if code != "000000" else "111111"
        for remaining in (2, 1):
            with self.assertRaises(CodeError) as error:
                self.store.verify("alice@example.com", "register", wrong)
            self.assertEqual(error.exception.code, "code_mismatch")
            self.assertIn(f"再试 {remaining} 次", error.exception.message)
        with self.assertRaises(CodeError) as locked:
            self.store.verify("alice@example.com", "register", wrong)
        self.assertEqual(locked.exception.code, "code_attempts")
        # The correct code is gone once the attempt budget is spent.
        with self.assertRaises(CodeError):
            self.store.verify("alice@example.com", "register", code)

    def test_resend_cooldown_then_new_code(self) -> None:
        self.store.issue("alice@example.com", "register")
        with self.assertRaises(CodeError) as early:
            self.store.issue("alice@example.com", "register")
        self.assertEqual(early.exception.status, 429)
        self.assertEqual(early.exception.code, "code_too_soon")
        self.clock.advance(61)
        code = self.store.issue("alice@example.com", "register")
        self.store.verify("alice@example.com", "register", code)

    def test_hourly_cap(self) -> None:
        for _ in range(5):
            self.store.issue("alice@example.com", "register")
            self.clock.advance(61)
        with self.assertRaises(CodeError) as capped:
            self.store.issue("alice@example.com", "register")
        self.assertEqual(capped.exception.status, 429)
        self.assertEqual(capped.exception.code, "code_rate_limited")

    def test_release_allows_an_immediate_retry(self) -> None:
        self.store.issue("alice@example.com", "register")
        self.store.release("alice@example.com", "register")
        code = self.store.issue("alice@example.com", "register")
        self.store.verify("alice@example.com", "register", code)

    def test_bad_code_format_is_rejected(self) -> None:
        self.store.issue("alice@example.com", "register")
        for bad in ("", "12345", "abcdef", "1234567"):
            with self.assertRaises(CodeError):
                self.store.verify("alice@example.com", "register", bad)


class MailerTests(unittest.TestCase):
    def test_console_transport_prints_the_code(self) -> None:
        settings = replace(
            load_settings(),
            mail_transport="console",
            mail_user="3034700847@qq.com",
            mail_password="",
            code_ttl=600,
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            Mailer(settings).send_code(
                "alice@example.com", "123456", "register"
            )
        output = buffer.getvalue()
        self.assertIn("123456", output)
        self.assertIn("alice@example.com", output)
        self.assertIn("register", output)

    def test_unconfigured_smtp_fails_before_connecting(self) -> None:
        settings = replace(
            load_settings(),
            mail_transport="smtp",
            mail_user="",
            mail_password="",
            mail_from="",
        )
        self.assertFalse(settings.mail_configured)
        with self.assertRaises(MailError) as error:
            Mailer(settings).send_code(
                "alice@example.com", "123456", "reset"
            )
        self.assertEqual(error.exception.code, "mail_not_configured")

    def test_qq_defaults_and_sender(self) -> None:
        settings = replace(
            load_settings(),
            mail_transport="smtp",
            mail_host="smtp.qq.com",
            mail_port=465,
            mail_security="ssl",
            mail_user="3034700847@qq.com",
            mail_password="authorization-code",
            mail_from="3034700847@qq.com",
        )
        self.assertTrue(settings.mail_configured)
        self.assertEqual(settings.mail_sender, "3034700847@qq.com")
        message = Mailer(settings)._build_message(
            "alice@example.com",
            "123456",
            "register",
            "注册行间账号",
            "你正在注册行间账号。",
            "如果这不是你本人的操作，请忽略这封邮件。",
            10,
        )
        self.assertIn("3034700847@qq.com", message["From"])
        self.assertEqual(message["To"], "alice@example.com")
        self.assertIn("123456", message["Subject"])
        bodies = [
            part.get_content()
            for part in message.walk()
            if part.get_content_type() in ("text/plain", "text/html")
        ]
        self.assertTrue(any("123456" in body for body in bodies))


if __name__ == "__main__":
    unittest.main()
