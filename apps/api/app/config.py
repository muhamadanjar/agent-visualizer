"""Strict environment-backed settings for the private single-owner pilot."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

def _dotenv_path(module_path: Path) -> Path:
    parents = module_path.resolve().parents
    project_root = parents[3] if len(parents) > 3 else parents[1]
    return project_root / ".env"


ENV_FILE = _dotenv_path(Path(__file__))

OWNER_USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
WEAK_SECRETS = {
    "changeme",
    "change-me",
    "change-me-in-production",
    "example",
    "password",
    "secret",
    "your-secret-here",
    "development-secret",
}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Raised when runtime configuration is missing or unsafe."""


def _required(environ: dict[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _bool(environ: dict[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be true or false")


def _int(environ: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def normalize_origin(raw: str) -> str:
    """Return a canonical scheme://host[:port] origin, rejecting paths and credentials."""
    value = raw.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError("PUBLIC_ORIGIN must use http or https")
    if not parsed.hostname:
        raise ConfigError("PUBLIC_ORIGIN must include a host")
    if parsed.username or parsed.password:
        raise ConfigError("PUBLIC_ORIGIN must not include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("PUBLIC_ORIGIN must be a bare origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("PUBLIC_ORIGIN has an invalid port") from exc

    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def _validate_database_url(value: str) -> None:
    normalized = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "postgresql"
        or not parsed.hostname
        or not parsed.username
        or parsed.password is None
        or not parsed.path.strip("/")
    ):
        raise ConfigError("DATABASE_URL must be a PostgreSQL URL with credentials and database")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("DATABASE_URL has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError("DATABASE_URL has an invalid port")


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    owner_username: str
    owner_password_hash: str
    session_secret: str
    public_origin: str
    database_url: str
    session_cookie_secure: bool = True
    session_ttl_seconds: int = 28800
    session_cookie_samesite: str = "strict"
    log_level: str = "INFO"
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 900
    runner_shared_token: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    def __post_init__(self) -> None:
        self._validate()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def _validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ConfigError("APP_ENV must be development, test, or production")
        if not OWNER_USERNAME_RE.fullmatch(self.owner_username):
            raise ConfigError("OWNER_USERNAME must be 1-64 safe ASCII characters")
        if not self.owner_password_hash.startswith("$argon2id$"):
            raise ConfigError("OWNER_PASSWORD_HASH must be an Argon2id hash")

        minimum = 32 if self.is_production else 16
        if len(self.session_secret) < minimum:
            raise ConfigError(f"SESSION_SECRET must be at least {minimum} characters")
        if self.session_secret.lower() in WEAK_SECRETS or len(set(self.session_secret)) < 8:
            raise ConfigError("SESSION_SECRET is too weak")

        if not 300 <= self.session_ttl_seconds <= 28800:
            raise ConfigError("SESSION_TTL_SECONDS must be between 300 and 28800")
        if self.session_cookie_samesite not in {"lax", "strict"}:
            raise ConfigError("SESSION_COOKIE_SAMESITE must be lax or strict")
        if self.is_production and not self.session_cookie_secure:
            raise ConfigError("SESSION_COOKIE_SECURE must be enabled in production")
        if self.public_origin != normalize_origin(self.public_origin):
            raise ConfigError("PUBLIC_ORIGIN must be normalized")
        if self.is_production and not self.public_origin.startswith("https://"):
            raise ConfigError("PUBLIC_ORIGIN must use https in production")
        if self.log_level.upper() not in LOG_LEVELS:
            raise ConfigError("LOG_LEVEL is invalid")
        if not 1 <= self.login_rate_limit_attempts <= 20:
            raise ConfigError("LOGIN_RATE_LIMIT_ATTEMPTS must be between 1 and 20")
        if not 60 <= self.login_rate_limit_window_seconds <= 3600:
            raise ConfigError("LOGIN_RATE_LIMIT_WINDOW_SECONDS must be between 60 and 3600")
        if self.runner_shared_token is not None and len(self.runner_shared_token) < 32:
            raise ConfigError("RUNNER_SHARED_TOKEN must be at least 32 characters when set")
        redis = urlsplit(self.redis_url)
        if redis.scheme not in {"redis", "rediss"} or not redis.hostname:
            raise ConfigError("REDIS_URL must use redis or rediss and include a host")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if environ is None else environ)
        environment = (env.get("APP_ENV") or "development").strip().lower()
        origin = normalize_origin(_required(env, "PUBLIC_ORIGIN"))
        return cls(
            environment=environment,
            owner_username=_required(env, "OWNER_USERNAME"),
            owner_password_hash=_required(env, "OWNER_PASSWORD_HASH"),
            session_secret=_required(env, "SESSION_SECRET"),
            public_origin=origin,
            database_url=_required(env, "DATABASE_URL"),
            session_cookie_secure=_bool(env, "SESSION_COOKIE_SECURE", environment == "production"),
            session_ttl_seconds=_int(env, "SESSION_TTL_SECONDS", 28800, 300, 28800),
            session_cookie_samesite=(env.get("SESSION_COOKIE_SAMESITE") or "strict").strip().lower(),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
            login_rate_limit_attempts=_int(env, "LOGIN_RATE_LIMIT_ATTEMPTS", 5, 1, 20),
            login_rate_limit_window_seconds=_int(
                env, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900, 60, 3600
            ),
            runner_shared_token=(env.get("RUNNER_SHARED_TOKEN") or "").strip() or None,
            redis_url=(env.get("REDIS_URL") or "redis://localhost:6379/0").strip(),
        )


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    if environ is None:
        env = {
            key: value
            for key, value in dotenv_values(ENV_FILE).items()
            if value is not None
        }
        env.update(os.environ)
    else:
        env = environ
    return Settings.from_env(env)
