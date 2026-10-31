from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from langchain_core.tools import tool

from runtime import (
    MAX_HTTP_RESPONSE_BYTES,
    LangChainRuntimeError,
    ProviderEndpointError,
    ProviderResponseLimitError,
    build_agent,
)


@pytest.mark.parametrize(
    ("model_id", "route", "temperature"),
    [("openrouter/example:free", "free", 0.2), ("openai-codex/gpt-5.6-luna", None, None)],
)
def test_factory_builds_agent_from_selected_model_route_and_tools(
    model_id: str, route: str | None, temperature: float | None
) -> None:
    requests: list[dict[str, Any]] = []

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer local-test-key"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if len(requests) == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-read",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "README.md"}),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            else:
                assert any(message.get("role") == "tool" for message in request["messages"])
                message = {"role": "assistant", "content": "Factory completed the task."}
                finish_reason = "stop"
            body = json.dumps(
                {
                    "id": f"chatcmpl-{len(requests)}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "router-free-model",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish_reason}
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    @tool
    def read_file(path: str) -> str:
        """Read one file from the assigned workspace."""
        assert path == "README.md"
        return "workspace content"

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        agent = build_agent(
            runtime_id="langchain",
            model_id=model_id,
            endpoint_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="local-test-key",
            route=route,
            temperature=temperature,
            system_prompt="Use the selected workspace tool to complete the request.",
            tools=[read_file],
            max_model_calls=2,
            max_tool_calls=1,
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "Read README.md."}]}
        )

        assert result["messages"][-1].content == "Factory completed the task."
        assert len(requests) == 2
        assert requests[0]["model"] == model_id
        assert requests[0].get("route") == route
        assert requests[0].get("temperature") == temperature
        assert [item["function"]["name"] for item in requests[0]["tools"]] == [
            "read_file"
        ]
        assert any(
            item.get("role") == "tool" and "workspace content" in item.get("content", "")
            for item in requests[1]["messages"]
        )
        assert all("local-test-key" not in json.dumps(item["messages"]) for item in requests)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_factory_rejects_unregistered_runtime_before_provider_access() -> None:
    with pytest.raises(LangChainRuntimeError, match="runtime"):
        build_agent(
            runtime_id="unknown",
            model_id="model",
            endpoint_url="https://provider.example/v1",
            api_key="not-used",
            route=None,
            temperature=None,
            system_prompt="prompt",
            tools=[],
            max_model_calls=1,
            max_tool_calls=1,
        )


def _build_local_test_agent(endpoint: str) -> Any:
    return build_agent(
        runtime_id="langchain",
        model_id="local/test",
        endpoint_url=endpoint,
        api_key="test-key",
        route=None,
        temperature=None,
        system_prompt="Return a concise completion.",
        tools=[],
        max_model_calls=1,
        max_tool_calls=1,
    )


def test_provider_redirect_is_rejected_without_following() -> None:
    paths: list[str] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            paths.append(self.path)
            self.send_response(307)
            self.send_header("Location", "/redirect-target")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        agent = _build_local_test_agent(f"http://127.0.0.1:{server.server_port}/v1")
        with pytest.raises(ProviderEndpointError, match="redirect"):
            agent.invoke({"messages": [{"role": "user", "content": "Complete."}]})
        assert paths == ["/v1/chat/completions"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_provider_response_size_limit_applies_before_json_parsing() -> None:
    oversized_body = b"x" * (MAX_HTTP_RESPONSE_BYTES + 1)

    class OversizedResponseHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(oversized_body)))
            self.end_headers()
            self.wfile.write(oversized_body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), OversizedResponseHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        agent = _build_local_test_agent(f"http://127.0.0.1:{server.server_port}/v1")
        with pytest.raises(ProviderResponseLimitError, match="size limit"):
            agent.invoke({"messages": [{"role": "user", "content": "Complete."}]})
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
