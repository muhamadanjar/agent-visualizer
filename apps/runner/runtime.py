from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import os
import threading
from typing import Any, Protocol


import httpx2

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict


TOOL_NAMES = frozenset({"list_files", "read_file", "write_file"})

MAX_HTTP_RESPONSE_BYTES = 1_048_576


class WorkspaceToolExecutor(Protocol):
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class _ListFilesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "."


class _PathArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class _WriteFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


def build_workspace_tools(
    workspace: WorkspaceToolExecutor,
    allowed_tools: Sequence[str],
    on_tool_action: Callable[[str], None],
    *,
    max_tool_calls: int,
) -> list[BaseTool]:
    if (
        max_tool_calls < 1
        or len(allowed_tools) != len(set(allowed_tools))
        or not set(allowed_tools).issubset(TOOL_NAMES)
    ):
        raise LangChainRuntimeError("tools")

    lock = threading.Lock()
    calls = 0

    def execute(name: str, arguments: dict[str, str]) -> str:
        nonlocal calls
        with lock:
            calls += 1
            if calls > max_tool_calls:
                raise LangChainRuntimeError("tool_limit")
            try:
                result = workspace.call(name, arguments)
            except (ValueError, OSError, UnicodeDecodeError):
                result = {"error": "Tool arguments or workspace operation were rejected."}
            on_tool_action(name)
            return json.dumps(result, ensure_ascii=False)

    def list_files(path: str = ".") -> str:
        return execute("list_files", {"path": path})

    def read_file(path: str) -> str:
        return execute("read_file", {"path": path})

    def write_file(path: str, content: str) -> str:
        return execute("write_file", {"path": path, "content": content})

    available = {
        "list_files": StructuredTool.from_function(
            list_files,
            name="list_files",
            description="List non-sensitive files in the project workspace.",
            args_schema=_ListFilesArguments,
        ),
        "read_file": StructuredTool.from_function(
            read_file,
            name="read_file",
            description="Read one UTF-8 project file up to 1 MiB.",
            args_schema=_PathArguments,
        ),
        "write_file": StructuredTool.from_function(
            write_file,
            name="write_file",
            description="Atomically write one UTF-8 project file up to 1 MiB.",
            args_schema=_WriteFileArguments,
        ),
    }
    return [available[name] for name in sorted(allowed_tools)]


class LangChainRuntimeError(ValueError):
    """A Run snapshot cannot initialize the configured LangChain runtime."""


class ProviderEndpointError(RuntimeError):
    """The provider response attempted to redirect the configured endpoint."""


class ProviderResponseLimitError(RuntimeError):
    """The provider response exceeded the bounded decoded-body size."""


def _guard_provider_response(response: httpx2.Response) -> None:
    if response.has_redirect_location:
        raise ProviderEndpointError("provider redirect rejected")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = -1
        if declared_length > MAX_HTTP_RESPONSE_BYTES:
            raise ProviderResponseLimitError("provider response exceeded size limit")
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > MAX_HTTP_RESPONSE_BYTES:
            raise ProviderResponseLimitError("provider response exceeded size limit")
        body.extend(chunk)
    response._content = bytes(body)


PROVIDER_HTTP_CLIENT = httpx2.Client(
    proxy=os.environ.get("RUNTIME_PROXY_URL") or None,
    follow_redirects=False,
    trust_env=False,
    event_hooks={"response": [_guard_provider_response]},
)
def build_agent(
    *,
    runtime_id: str,
    model_id: str,
    endpoint_url: str,
    api_key: str,
    route: str | None,
    temperature: float | None,
    system_prompt: str,
    tools: Sequence[BaseTool],
    max_model_calls: int,
    max_tool_calls: int,
    timeout_seconds: float = 30,
) -> Any:
    """Create one executable LangChain agent from a Run's immutable settings."""
    if runtime_id != "langchain":
        raise LangChainRuntimeError("runtime")
    if not model_id.strip() or not endpoint_url.strip() or not api_key:
        raise LangChainRuntimeError("model_configuration")
    if not system_prompt.strip() or max_model_calls < 1 or max_tool_calls < 1:
        raise LangChainRuntimeError("execution_limits")
    if timeout_seconds <= 0:
        raise LangChainRuntimeError("execution_limits")

    model_options: dict[str, object] = {
        "model": model_id,
        "base_url": endpoint_url,
        "api_key": api_key,
        "max_retries": 0,
        "max_tokens": 1024,
        "timeout": timeout_seconds,
        "http_client": PROVIDER_HTTP_CLIENT,
    }
    if temperature is not None:
        model_options["temperature"] = temperature
    if route is not None:
        model_options["extra_body"] = {"route": route}

    try:
        model = ChatOpenAI(**model_options)
        return create_agent(
            model=model,
            tools=list(tools),
            system_prompt=system_prompt,
            middleware=[
                ModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="error"),
                ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error"),
            ],
        )
    except LangChainRuntimeError:
        raise
    except Exception as error:
        raise LangChainRuntimeError("model_configuration") from error
