"""Verification-code email delivery.

The default transport is QQ Mail SMTP (``smtp.qq.com``). QQ does not
accept the mailbox login password for SMTP: open the mailbox settings,
enable the POP3/IMAP/SMTP service, generate an "授权码", and put that
code in ``SMTP_PASSWORD``. Nothing here is exposed to the browser.

``MAIL_TRANSPORT=console`` prints the code to the server console instead
of sending it. It exists for local development so the registration and
password-reset flow can be tested before the authorization code is set.
"""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle is typing-only
    from config import Settings

PURPOSE_COPY = {
    "register": (
        "注册行间账号",
        "你正在注册行间账号。",
        "如果这不是你本人的操作，请忽略这封邮件，你的邮箱不会被绑定。",
    ),
    "reset": (
        "重置行间密码",
        "你正在重置行间账号的密码。",
        "如果这不是你本人的操作，请忽略这封邮件，原密码仍然有效。",
    ),
}


class MailError(Exception):
    """A delivery problem that should be shown to the user (safely)."""

    def __init__(
        self,
        message: str,
        status: int = 502,
        code: str = "mail_failed",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.detail = detail


class Mailer:
    """Send one short verification email through the configured account."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings.mail_configured

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def send_code(self, to_email: str, code: str, purpose: str) -> None:
        settings = self.settings
        title, lead, advice = PURPOSE_COPY.get(
            purpose,
            ("行间验证码", "你正在进行一项账号操作。", "如果不是你本人操作，请忽略这封邮件。"),
        )
        minutes = max(1, round(settings.code_ttl / 60))
        if settings.mail_transport == "console":
            self._print_console(to_email, code, purpose, minutes)
            return
        if not self.configured:
            raise MailError(
                "邮箱服务未配置，暂时无法发送验证码。请联系管理员。",
                status=503,
                code="mail_not_configured",
            )
        message = self._build_message(
            to_email, code, purpose, title, lead, advice, minutes
        )
        self._deliver(message)

    # ------------------------------------------------------------------
    # Message construction
    # ------------------------------------------------------------------
    def _build_message(
        self,
        to_email: str,
        code: str,
        purpose: str,
        title: str,
        lead: str,
        advice: str,
        minutes: int,
    ) -> EmailMessage:
        settings = self.settings
        sender = settings.mail_sender
        message = EmailMessage()
        message["Subject"] = f"行间 · {title}验证码 {code}"
        message["From"] = formataddr(
            (settings.mail_from_name, sender), charset="utf-8"
        )
        message["To"] = to_email
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="xingjian.local")
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(
            f"{lead}\n\n"
            f"验证码：{code}\n\n"
            f"这个验证码 {minutes} 分钟内有效，只能使用一次。\n"
            f"请勿把验证码告诉任何人。\n\n"
            f"{advice}\n\n"
            "—— 行间\n"
        )
        safe_code = escape(code)
        safe_lead = escape(lead)
        safe_advice = escape(advice)
        message.add_alternative(
            f"""\
<!doctype html>
<html lang="zh-CN"><body style="margin:0;padding:24px;background:#f7faf8;\
font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#243b35">
<div style="max-width:460px;margin:0 auto;padding:28px;background:#fff;\
border:1px solid #e3e9e5;border-radius:16px">
<p style="margin:0 0 6px;font-size:13px;letter-spacing:4px;color:#326451">行间</p>
<h1 style="margin:0 0 18px;font-size:18px;font-weight:600">邮箱验证码</h1>
<p style="margin:0 0 18px;font-size:14px;line-height:1.7;color:#667770">\
{safe_lead}</p>
<p style="margin:0;padding:16px;text-align:center;background:#eaf2ed;\
border-radius:10px;font-size:30px;letter-spacing:8px;font-weight:600;\
color:#326451">{safe_code}</p>
<p style="margin:18px 0 0;font-size:13px;line-height:1.7;color:#667770">\
验证码 <strong>{minutes} 分钟</strong>内有效，只能使用一次。\
请勿把验证码告诉任何人。</p>
<p style="margin:14px 0 0;font-size:12px;line-height:1.7;color:#8a9a93">\
{safe_advice}</p>
</div>
<p style="max-width:460px;margin:14px auto 0;text-align:center;\
font-size:11px;color:#9aa8a1">这封邮件由系统自动发送，请勿直接回复。</p>
</body></html>""",
            subtype="html",
        )
        return message

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    def _deliver(self, message: EmailMessage) -> None:
        settings = self.settings
        context = ssl.create_default_context()
        timeout = max(5.0, float(settings.mail_timeout))
        server = None
        try:
            if settings.mail_security == "ssl":
                server = smtplib.SMTP_SSL(
                    settings.mail_host,
                    settings.mail_port,
                    timeout=timeout,
                    context=context,
                )
            else:
                server = smtplib.SMTP(
                    settings.mail_host,
                    settings.mail_port,
                    timeout=timeout,
                )
                server.ehlo()
                if settings.mail_security == "starttls":
                    server.starttls(context=context)
                    server.ehlo()
            server.login(settings.mail_user, settings.mail_password)
            server.send_message(message)
        except smtplib.SMTPAuthenticationError as error:
            self._log(
                "SMTP 认证失败：请确认 SMTP_PASSWORD 是 QQ 邮箱的授权码，"
                "不是邮箱登录密码，并且已在 QQ 邮箱设置中开启 SMTP 服务。"
            )
            raise MailError(
                "邮箱授权码无效，验证码发送失败。请联系管理员检查 "
                "SMTP_PASSWORD。",
                status=503,
                code="mail_auth",
                detail=str(error),
            ) from error
        except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
            self._log(
                f"SMTP 发送失败（{settings.mail_host}:"
                f"{settings.mail_port}）：{type(error).__name__}: {error}"
            )
            raise MailError(
                "验证码邮件发送失败，请稍后再试。",
                status=502,
                code="mail_failed",
                detail=f"{type(error).__name__}: {error}",
            ) from error
        finally:
            if server is not None:
                try:
                    server.quit()
                except (smtplib.SMTPException, OSError):
                    pass

    # ------------------------------------------------------------------
    # Local development transport
    # ------------------------------------------------------------------
    @staticmethod
    def _print_console(
        to_email: str, code: str, purpose: str, minutes: int
    ) -> None:
        sys.stdout.write(
            f"[mail] {to_email} · {purpose} · 验证码 {code} "
            f"（{minutes} 分钟内有效）\n"
        )
        sys.stdout.flush()

    @staticmethod
    def _log(message: str) -> None:
        sys.stdout.write(f"[mail] {message}\n")
        sys.stdout.flush()
