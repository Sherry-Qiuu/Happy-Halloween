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

    @property
    def configured(self) -> bool:
        """True when the service can answer real requests."""
        return self.mock or bool(self.api_key)

    @property
    def chat_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

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
    )
