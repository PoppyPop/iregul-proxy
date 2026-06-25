"""Tests for API handlers exposed by APIServer."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from iregul_proxy.api import APIServer


class FakeDownstreamHandler:
    """Minimal downstream handler stub for API tests."""

    def __init__(self, connected: bool):
        self._connected = connected

    async def is_connected(self) -> bool:
        return self._connected


class FakeProxyServer:
    """Minimal proxy server stub for API tests."""

    def __init__(
        self,
        *,
        last_data: dict[str, Any] | None,
        connected: bool,
        command_response: str = "ok-response",
        command_error: Exception | None = None,
    ):
        self._last_data = last_data
        self.downstream_handler = FakeDownstreamHandler(connected)
        self._command_response = command_response
        self._command_error = command_error
        self.executed_commands: list[str] = []

        class _FakeLocalCommandHandler:
            def __init__(self, parent: FakeProxyServer) -> None:
                self._parent = parent

            async def execute_command(self, external_command: str) -> str:
                self._parent.executed_commands.append(external_command)
                if self._parent._command_error is not None:
                    raise self._parent._command_error
                return self._parent._command_response

        self.local_command_handler = _FakeLocalCommandHandler(self)

    def get_last_data(self) -> dict[str, Any] | None:
        return self._last_data

def build_test_client(proxy: FakeProxyServer) -> TestClient:
    """Build a test client for an APIServer bound to a fake proxy."""
    api_server = APIServer(host="127.0.0.1", port=8080, proxy_server=proxy)
    return TestClient(api_server.app)


def test_get_data_handler_returns_no_data_when_proxy_has_none() -> None:
    """GET /api/data returns no_data when the proxy has not received frames yet."""
    proxy = FakeProxyServer(last_data=None, connected=False)
    client = build_test_client(proxy)

    response = client.get("/api/data")

    assert response.status_code == 200
    assert response.json() == {
        "status": "no_data",
        "data": None,
        "message": "No data received yet from heat pump",
    }


def test_get_data_handler_returns_latest_data() -> None:
    """GET /api/data returns the latest decoded payload when present."""
    last_data = {
        "timestamp": "2026-06-25T08:00:00",
        "is_old": False,
        "count": 1,
        "groups": [],
        "raw": "{200#}",
    }
    proxy = FakeProxyServer(last_data=last_data, connected=False)
    client = build_test_client(proxy)

    response = client.get("/api/data")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "data": last_data,
        "message": None,
    }


def test_health_check_handler_returns_downstream_connection_state() -> None:
    """GET /api/health reports healthy status and proxy connection state."""
    proxy = FakeProxyServer(last_data=None, connected=True)
    client = build_test_client(proxy)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "proxy_running": True,
    }


def test_send_command_handler_returns_response_when_command_succeeds() -> None:
    """POST /api/command returns ok with downstream response payload."""
    proxy = FakeProxyServer(
        last_data=None,
        connected=True,
        command_response="26/06/2026 12:34:56{reply#}",
    )
    client = build_test_client(proxy)

    response = client.post("/api/command", json={"command": "502"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "response": "26/06/2026 12:34:56{reply#}",
        "message": None,
    }
    assert proxy.executed_commands == ["502"]


def test_send_command_handler_returns_error_on_value_error() -> None:
    """POST /api/command maps ValueError from proxy to error response."""
    proxy = FakeProxyServer(
        last_data=None,
        connected=False,
        command_error=ValueError("No downstream connection available"),
    )
    client = build_test_client(proxy)

    response = client.post("/api/command", json={"command": "200"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "response": None,
        "message": "No downstream connection available",
    }


def test_send_command_handler_returns_timeout_on_timeout_error() -> None:
    """POST /api/command maps TimeoutError from proxy to timeout response."""
    proxy = FakeProxyServer(
        last_data=None,
        connected=True,
        command_error=TimeoutError(),
    )
    client = build_test_client(proxy)

    response = client.post("/api/command", json={"command": "{200#}"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "timeout",
        "response": None,
        "message": "Heat pump did not respond in time",
    }
