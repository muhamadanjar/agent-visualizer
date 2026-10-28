from __future__ import annotations

import ipaddress
import os
import select
import socket
import socketserver
from urllib.parse import urlsplit

MAX_HEADER_BYTES = 32 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
LOCAL_GATEWAY_HOST = "host.docker.internal"


def _destination(host: str, port: int) -> tuple[str, int]:
    allowed_host = os.environ.get("ALLOW_HOST", "").lower().rstrip(".")
    try:
        allowed_port = int(os.environ.get("ALLOW_PORT", "0"))
    except ValueError as error:
        raise ValueError("proxy destination configuration is invalid") from error
    normalized = host.lower().rstrip(".")
    if not allowed_host or normalized != allowed_host or port != allowed_port:
        raise ValueError("destination is not allowlisted")
    if normalized == LOCAL_GATEWAY_HOST and port == 20128:
        records = socket.getaddrinfo(normalized, port, type=socket.SOCK_STREAM)
        if not records:
            raise ValueError("local Model Endpoint is unavailable")
        return records[0][4][0], port
    try:
        address = ipaddress.ip_address(normalized)
        addresses = [str(address)]
    except ValueError:
        records = socket.getaddrinfo(normalized, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    if not parsed_addresses or any(not address.is_global for address in parsed_addresses):
        raise ValueError("destination resolves to a non-public address")
    return addresses[0], port


def _parse_authority(value: str, default_port: int) -> tuple[str, int]:
    if not value or "@" in value or any(character in value for character in "\r\n \t"):
        raise ValueError("proxy destination authority is invalid")
    parsed = urlsplit("//" + value)
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("proxy destination authority is invalid")
    try:
        port = parsed.port or default_port
    except ValueError as error:
        raise ValueError("proxy destination port is invalid") from error
    if not 1 <= port <= 65535:
        raise ValueError("proxy destination port is invalid")
    return parsed.hostname, port


def _http_request_target(target: str, host_header: str | None) -> tuple[str, int, str]:
    parsed = urlsplit(target)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or host_header is None
    ):
        raise ValueError("HTTP proxy request target is invalid")
    try:
        port = parsed.port or 80
    except ValueError as error:
        raise ValueError("HTTP proxy destination port is invalid") from error
    header_host, header_port = _parse_authority(host_header, 80)
    if (
        header_host.lower().rstrip(".") != parsed.hostname.lower().rstrip(".")
        or header_port != port
    ):
        raise ValueError("HTTP Host header does not match the request target")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed.hostname, port, path


class EgressProxyHandler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self) -> None:
        self.connection.settimeout(30)
        try:
            request_line = self.rfile.readline(MAX_HEADER_BYTES + 1)
            if len(request_line) > MAX_HEADER_BYTES or not request_line.endswith(b"\r\n"):
                self._reject(400, "Bad Request")
                return
            try:
                method, target, version = request_line.decode("ascii").rstrip("\r\n").split(" ", 2)
            except (UnicodeDecodeError, ValueError):
                self._reject(400, "Bad Request")
                return
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                self._reject(400, "Bad Request")
                return
            if method.upper() == "CONNECT":
                self._handle_connect(target)
            else:
                self._handle_http(method, target, version)
        except (OSError, ValueError):
            try:
                self._reject(502, "Bad Gateway")
            except OSError:
                pass

    def _handle_connect(self, target: str) -> None:
        header_bytes = 0
        while True:
            line = self.rfile.readline(MAX_HEADER_BYTES + 1)
            header_bytes += len(line)
            if (
                len(line) > MAX_HEADER_BYTES
                or header_bytes > MAX_HEADER_BYTES
                or not line.endswith(b"\r\n")
            ):
                self._reject(400, "Bad Request")
                return
            if line == b"\r\n":
                break
            if b":" not in line:
                self._reject(400, "Bad Request")
                return
        host, port = _parse_authority(target, 443)
        host, port = _destination(host, port)
        upstream = socket.create_connection((host, port), timeout=15)
        upstream.settimeout(30)
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.wfile.flush()
        self._relay(upstream)
    def _handle_http(self, method: str, target: str, version: str) -> None:
        body_length = 0
        content_length_seen = False
        host_header: str | None = None
        header_bytes = 0
        headers: list[bytes] = []
        while True:
            line = self.rfile.readline(MAX_HEADER_BYTES + 1)
            header_bytes += len(line)
            if len(line) > MAX_HEADER_BYTES or header_bytes > MAX_HEADER_BYTES or not line.endswith(b"\r\n"):
                self._reject(400, "Bad Request")
                return
            if line == b"\r\n":
                break
            try:
                name, value = line.split(b":", 1)
                header_name = name.decode("ascii").strip().lower()
                header_value = value.decode("latin-1").strip()
            except (UnicodeDecodeError, ValueError):
                self._reject(400, "Bad Request")
                return
            if header_name in {"proxy-authorization", "proxy-connection", "connection"}:
                continue
            if header_name == "host":
                if host_header is not None:
                    self._reject(400, "Bad Request")
                    return
                host_header = header_value
            if header_name == "transfer-encoding":
                self._reject(501, "Not Implemented")
                return
            if header_name == "content-length":
                if content_length_seen or not header_value.isdigit():
                    self._reject(400, "Bad Request")
                    return
                content_length_seen = True
                body_length = int(header_value)
            headers.append(name + b": " + value.strip() + b"\r\n")

        try:
            destination, port, path = _http_request_target(target, host_header)
            host, port = _destination(destination, port)
        except ValueError:
            self._reject(403, "Forbidden")
            return
        if body_length > MAX_REQUEST_BYTES:
            self._reject(413, "Payload Too Large")
            return
        upstream = socket.create_connection((host, port), timeout=15)
        upstream.settimeout(30)
        upstream.sendall(f"{method} {path} {version}\r\n".encode("ascii"))
        for header in headers:
            upstream.sendall(header)
        upstream.sendall(b"Connection: close\r\n\r\n")
        remaining = body_length
        while remaining:
            chunk = self.rfile.read(min(64 * 1024, remaining))
            if not chunk:
                upstream.close()
                self._reject(400, "Bad Request")
                return
            upstream.sendall(chunk)
            remaining -= len(chunk)
        self._relay(upstream)

    def _relay(self, upstream: socket.socket) -> None:
        client = self.connection
        try:
            while True:
                readable, _, _ = select.select((client, upstream), (), (), 30)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    destination = upstream if source is client else client
                    destination.sendall(data)
        finally:
            upstream.close()

    def _reject(self, status: int, phrase: str) -> None:
        body = f"{status} {phrase}\n".encode("ascii")
        self.wfile.write(
            f"HTTP/1.1 {status} {phrase}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )
        self.wfile.flush()


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    port = int(os.environ.get("PROXY_LISTEN_PORT", "3128"))
    if not os.environ.get("ALLOW_HOST") or not 1 <= port <= 65535:
        raise RuntimeError("proxy allowlist configuration is required")
    with ThreadedServer(("0.0.0.0", port), EgressProxyHandler) as server:
        server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
