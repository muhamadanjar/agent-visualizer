from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID


ADAPTER = "openai_compatible_chat_completions"
LOCAL_PROVIDER_PORT = 20128
GATEWAY_ENDPOINT = "http://localhost:14000/v1"
GATEWAY_CREDENTIAL = "OMP_GATEWAY_KEY"
CREDENTIAL_ENVIRONMENTS = frozenset({"NINEROUTER_KEY", GATEWAY_CREDENTIAL})
ENDPOINT_NAME_RE = re.compile(r"[^\x00-\x1f]{1,120}\Z")
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}\Z")
ROUTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}\Z")


class ModelEndpointInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_endpoint_url(value: str) -> str:
    endpoint = value.strip()
    if not endpoint or len(endpoint) > 2048 or any(char.isspace() for char in endpoint):
        raise ModelEndpointInputError("endpoint_url")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ModelEndpointInputError("endpoint_url") from exc
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
        raise ModelEndpointInputError("endpoint_url")
    if port is not None and not 1 <= port <= 65535:
        raise ModelEndpointInputError("endpoint_url")
    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global and not (address.is_loopback and port == LOCAL_PROVIDER_PORT):
            raise ModelEndpointInputError("endpoint_url")
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
            raise ModelEndpointInputError("endpoint_url")
        if host == "localhost":
            if parsed.scheme != "http" or port != LOCAL_PROVIDER_PORT:
                raise ModelEndpointInputError("endpoint_url")
        elif parsed.scheme == "http":
            raise ModelEndpointInputError("endpoint_url")
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"


def normalize_model_id(value: str) -> str:
    model_id = value.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise ModelEndpointInputError("model_id")
    return model_id


def normalize_route(value: str | None) -> str | None:
    if value is None:
        return None
    route = value.strip()
    if not ROUTE_RE.fullmatch(route):
        raise ModelEndpointInputError("route")
    return route


def validate_provider_binding(
    credential_env: str, endpoint_url: str, model_id: str, route: str | None
) -> None:
    """Bind gateway credentials to one local endpoint and explicit Codex model."""
    if credential_env == GATEWAY_CREDENTIAL:
        if endpoint_url != GATEWAY_ENDPOINT:
            raise ModelEndpointInputError("endpoint_url")
        if not re.fullmatch(r"openai-codex/[A-Za-z0-9][A-Za-z0-9._+-]{0,186}", model_id):
            raise ModelEndpointInputError("model_id")
        if route is not None:
            raise ModelEndpointInputError("route")
    elif credential_env != "NINEROUTER_KEY":
        raise ModelEndpointInputError("credential_env")


@dataclass(frozen=True, slots=True)
class ModelEndpointDraft:
    name: str
    endpoint_url: str
    credential_env: str
    default_model_id: str
    default_route: str | None

    @classmethod
    def create(
        cls,
        name: str,
        endpoint_url: str,
        credential_env: str,
        default_model_id: str,
        default_route: str | None = None,
    ) -> ModelEndpointDraft:
        normalized_name = name.strip()
        if not ENDPOINT_NAME_RE.fullmatch(normalized_name):
            raise ModelEndpointInputError("name")
        if credential_env not in CREDENTIAL_ENVIRONMENTS:
            raise ModelEndpointInputError("credential_env")
        normalized_endpoint = (
            GATEWAY_ENDPOINT
            if credential_env == GATEWAY_CREDENTIAL and endpoint_url == GATEWAY_ENDPOINT
            else normalize_endpoint_url(endpoint_url)
        )
        model_id = normalize_model_id(default_model_id)
        route = normalize_route(default_route)
        validate_provider_binding(credential_env, normalized_endpoint, model_id, route)
        return cls(
            name=normalized_name,
            endpoint_url=normalized_endpoint,
            credential_env=credential_env,
            default_model_id=model_id,
            default_route=route,
        )


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    id: UUID
    owner_id: str
    name: str
    adapter: str
    endpoint_url: str
    credential_env: str
    default_model_id: str
    default_route: str | None
    archived_at: datetime | None
    created_at: datetime


class ModelEndpointRepository(Protocol):
    async def create(self, owner_id: str, draft: ModelEndpointDraft) -> ModelEndpoint: ...

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ) -> list[ModelEndpoint]: ...

    async def get_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None: ...

    async def archive_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None: ...
