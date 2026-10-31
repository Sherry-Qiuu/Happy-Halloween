"""Runtime configuration for the Xingjian DeepSeek agent service.

Every value comes from an environment variable. A local ``.env`` file is
read on start so ``python server.py`` works without exporting anything.
The API key never leaves the server process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` pairs from *path* into ``os.environ``."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _as_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(name, "").strip().lower()
    return value if value in allowed else default


def _clamped_int(name: str, default: int, low: int, high: int) -> int:
    return max(low, min(high, _as_int(name, default)))


def _cors_origins() -> tuple[str, ...]:
    raw = os.environ.get(
        "AGENT_CORS_ORIGINS",
        "null,http://127.0.0.1,http://localhost",
    )
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the service configuration."""

    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float
    host: str
    port: int
    mock: bool
    rate_limit: int
    max_history: int
    cors_origins: tuple[str, ...]
    web_dir: Path
    data_dir: Path
    session_days: int
    cookie_secure: bool
    allow_registration: bool
    trust_proxy: bool
    max_users: int

    # Email verification (registration and password reset). The SMTP
    # password is a QQ Mail "授权码", never the mailbox login password.
    mail_transport: str = "smtp"
    mail_host: str = "smtp.qq.com"
    mail_port: int = 465
    mail_security: str = "ssl"
    mail_user: str = ""
    mail_password: str = ""
    mail_from: str = ""
    mail_from_name: str = "行间"
    mail_timeout: float = 20.0
    code_ttl: int = 600
    code_resend: int = 60
    code_max_attempts: int = 5
    code_max_per_hour: int = 5

    @property
    def configured(self) -> bool:
        """True when the service can answer real requests."""
        return self.mock or bool(self.api_key)

    @property
    def chat_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def mail_configured(self) -> bool:
        """True when a verification code can actually be delivered.

        ``console`` is a local-development transport: the code is printed
        to the server console instead of sent, so the whole flow can be
        tested before a QQ Mail authorization code exists.
        """
        if self.mail_transport == "console":
            return True
        return bool(self.mail_user and self.mail_password)

    @property
    def mail_sender(self) -> str:
        return self.mail_from or self.mail_user

    def origin_allowed(self, origin: str) -> bool:
        if not origin:
            return False
        for allowed in self.cors_origins:
            if allowed == "*":
                return True
            if origin == allowed:
                return True
            if allowed.startswith("http") and origin.startswith(f"{allowed}:"):
                return True
        return False


def load_settings(env_file: Path | None = None) -> Settings:
    """Build a :class:`Settings` object from the environment."""
    load_env_file(env_file or (ROOT / ".env"))
    port = os.environ.get("PORT", "").strip() or os.environ.get(
        "AGENT_PORT", ""
    ).strip()
    mail_user = os.environ.get("SMTP_USER", "").strip()
    mail_password = os.environ.get("SMTP_PASSWORD", "").strip()
    mail_port = _as_int("SMTP_PORT", 465)
    if not 1 <= mail_port <= 65535:
        mail_port = 465
    return Settings(
        api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        base_url=os.environ.get(
            "DEEPSEEK_BASE_URL", DEFAULT_BASE_URL
        ).strip(),
        model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip(),
        temperature=_as_float("AGENT_TEMPERATURE", 0.4),
        max_tokens=_as_int("AGENT_MAX_TOKENS", 1200),
        timeout=_as_float("AGENT_TIMEOUT", 60.0),
        host=os.environ.get("HOST", "127.0.0.1").strip(),
        port=int(port) if port else 8000,
        mock=_as_bool("AGENT_MOCK"),
        rate_limit=_as_int("AGENT_RATE_LIMIT", 40),
        max_history=_as_int("AGENT_MAX_HISTORY", 8),
        cors_origins=_cors_origins(),
        web_dir=Path(
            os.environ.get("AGENT_WEB_DIR", str(ROOT / "web"))
        ).resolve(),
        data_dir=Path(
            os.environ.get(
                "XINGJIAN_DATA_DIR",
                str(ROOT.parent / "server information"),
            )
        ).resolve(),
        session_days=_as_int("XINGJIAN_SESSION_DAYS", 7),
        cookie_secure=_as_bool("COOKIE_SECURE"),
        allow_registration=_as_bool("XINGJIAN_ALLOW_REGISTRATION", True),
        trust_proxy=_as_bool("XINGJIAN_TRUST_PROXY"),
        max_users=_as_int("XINGJIAN_MAX_USERS", 5_000),
        mail_transport=_choice(
            "MAIL_TRANSPORT", "smtp", ("smtp", "console")
        ),
        mail_host=os.environ.get("SMTP_HOST", "smtp.qq.com").strip()
        or "smtp.qq.com",
        mail_port=mail_port,
        mail_security=_choice(
            "MAIL_SECURITY", "ssl", ("ssl", "starttls", "plain")
        ),
        mail_user=mail_user,
        mail_password=mail_password,
        mail_from=os.environ.get("MAIL_FROM", "").strip() or mail_user,
        mail_from_name=os.environ.get("MAIL_FROM_NAME", "行间").strip()
        or "行间",
        mail_timeout=_as_float("SMTP_TIMEOUT", 20.0),
        code_ttl=_clamped_int("EMAIL_CODE_TTL", 600, 60, 3_600),
        code_resend=_clamped_int("EMAIL_CODE_RESEND", 60, 10, 600),
        code_max_attempts=_clamped_int(
            "EMAIL_CODE_MAX_ATTEMPTS", 5, 3, 10
        ),
        code_max_per_hour=_clamped_int(
            "EMAIL_CODE_MAX_PER_HOUR", 5, 1, 20
        ),
    )
