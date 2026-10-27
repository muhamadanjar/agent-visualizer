from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit


ADAPTER = "openai_compatible_chat_completions"
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}\Z")
ROUTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}\Z")
LOCAL_PROVIDER_PORT = 20128


class ProviderConfigInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _normalize_endpoint_url(value: str) -> str:
    endpoint = value.strip()
    if not endpoint or len(endpoint) > 2048 or any(char.isspace() for char in endpoint):
        raise ProviderConfigInputError("endpoint_url")

    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ProviderConfigInputError("endpoint_url") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
    ):
        raise ProviderConfigInputError("endpoint_url")
    if port is not None and not 1 <= port <= 65535:
        raise ProviderConfigInputError("endpoint_url")

    host = parsed.hostname.lower().rstrip(".")
    if not host:
        raise ProviderConfigInputError("endpoint_url")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        is_allowed_loopback = address.is_loopback and port == LOCAL_PROVIDER_PORT
        if not address.is_global and not is_allowed_loopback:
            raise ProviderConfigInputError("endpoint_url")
    else:
        labels = host.split(".")
        if (
            ":" in host
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not re.fullmatch(r"[a-z0-9-]+", label)
                for label in labels
            )
            or all(label.isdecimal() for label in labels)
        ):
            raise ProviderConfigInputError("endpoint_url")
        if host == "localhost":
            if parsed.scheme != "http" or port != LOCAL_PROVIDER_PORT:
                raise ProviderConfigInputError("endpoint_url")
        elif parsed.scheme == "http":
            raise ProviderConfigInputError("endpoint_url")

    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{netloc}{path}"


def _normalize_model_id(value: str) -> str:
    model_id = value.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise ProviderConfigInputError("model_id")
    return model_id


def _normalize_route(value: str | None) -> str | None:
    if value is None:
        return None
    route = value.strip()
    if not ROUTE_RE.fullmatch(route):
        raise ProviderConfigInputError("route")
    return route


@dataclass(frozen=True, slots=True)
class ProviderConfigDraft:
    endpoint_url: str
    model_id: str
    route: str | None

    @classmethod
    def create(
        cls, endpoint_url: str, model_id: str, route: str | None = None
    ) -> ProviderConfigDraft:
        return cls(
            endpoint_url=_normalize_endpoint_url(endpoint_url),
            model_id=_normalize_model_id(model_id),
            route=_normalize_route(route),
        )


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    owner_id: str
    version: int
    adapter: str
    endpoint_url: str
    model_id: str
    route: str | None
    updated_at: datetime


class ProviderConfigRepository(Protocol):
    async def get_for_owner(self, owner_id: str) -> ProviderConfig | None: ...

    async def upsert_for_owner(
        self, owner_id: str, draft: ProviderConfigDraft
    ) -> ProviderConfig: ...
