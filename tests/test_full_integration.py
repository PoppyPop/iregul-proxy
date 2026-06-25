"""Full integration tests for upstream and local command routing."""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import AsyncGenerator
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest
from aioiregul.v2 import decoder

from iregul_proxy.proxy import ProxyServer

logger = logging.getLogger(__name__)


class MockUpstreamEndpoint:
    """Minimal upstream server endpoint controlled by tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self._connected = asyncio.Event()
        self._disconnect = asyncio.Event()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._connected.set()

        try:
            await self._disconnect.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        if self.server.sockets:
            self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._disconnect.set()
        if self._writer is not None and not self._writer.is_closing():
            self._writer.close()
            await self._writer.wait_closed()

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def wait_connected(self) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=2.0)

    async def send_request(self, payload: bytes) -> None:
        if self._writer is None:
            raise RuntimeError("Upstream writer is not connected")
        self._writer.write(payload)
        await self._writer.drain()

    async def read_response(self) -> bytes:
        if self._reader is None:
            raise RuntimeError("Upstream reader is not connected")
        return await asyncio.wait_for(self._reader.readuntil(b"}"), timeout=2.0)


@pytest.fixture
async def proxy_with_upstream() -> AsyncGenerator[tuple[ProxyServer, MockUpstreamEndpoint]]:
    """Start proxy server wired to a controllable upstream endpoint."""
    upstream = MockUpstreamEndpoint()
    await upstream.start()

    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host=upstream.host,
        upstream_port=upstream.port,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    try:
        yield proxy, upstream
    finally:
        await proxy.stop()
        await upstream.stop()


@pytest.fixture
async def proxy_without_upstream() -> AsyncGenerator[ProxyServer]:
    """Start proxy server with upstream forwarding disabled."""
    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host="127.0.0.1",
        upstream_port=65002,
        upstream_enabled=False,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    try:
        yield proxy
    finally:
        await proxy.stop()


async def test_full_routing_between_upstream_local_and_downstream(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route upstream and local requests to downstream and back to proper requester."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-upstream-response{u-ok#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="200",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-ok#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    try:
        # Upstream request path.
        await upstream.send_request(b"{200#}")
        upstream_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert upstream_to_downstream == b"{200#}"

        hp_writer.write(b"hp-upstream-response{u-ok#}")
        await hp_writer.drain()

        upstream_response = await upstream.read_response()
        assert upstream_response == b"hp-upstream-response{u-ok#}"

        # Local command path.
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-ok#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-ok#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_concurrent_upstream_and_local_commands_are_serialized_and_routed(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent upstream/local requests are serialized and answered correctly."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> Any:
        if text_data == "hp-upstream-response{u-conc#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="200",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-conc#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    async def send_local_and_read() -> str:
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()
            raw = await asyncio.wait_for(local_reader.read(1024), timeout=2.0)
            return raw.decode("utf-8", errors="ignore")
        finally:
            local_writer.close()
            await local_writer.wait_closed()

    try:
        # Trigger upstream first so it takes the request lock.
        await upstream.send_request(b"{200#}")
        first_downstream_request = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert first_downstream_request == b"{200#}"

        local_started_at = monotonic()
        local_task = asyncio.create_task(send_local_and_read())

        await asyncio.sleep(0.1)
        assert not local_task.done()

        await asyncio.sleep(0.2)
        hp_writer.write(b"hp-upstream-response{u-conc#}")
        await hp_writer.drain()

        upstream_response = await upstream.read_response()
        assert upstream_response == b"hp-upstream-response{u-conc#}"

        second_downstream_request = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert second_downstream_request == b"cdraminfoDEV1PWD1{10#}"

        hp_writer.write(b"hp-local-response{l-conc#}")
        await hp_writer.drain()

        local_response = await local_task
        assert re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-conc#\}$", local_response)
        assert monotonic() - local_started_at >= 0.2
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_downstream_keepalive_is_forwarded_to_upstream(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keepalive frames from downstream are forwarded unchanged to upstream."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-keepalive{ka#}":
            return SimpleNamespace(
                is_keepalive=True,
                message_type=None,
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    try:
        hp_writer.write(b"hp-keepalive{ka#}")
        await hp_writer.drain()

        upstream_payload = await upstream.read_response()
        assert upstream_payload == b"hp-keepalive{ka#}"
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()
        _ = hp_reader


async def test_downstream_keepalive_is_ignored_when_upstream_is_disabled(
    proxy_without_upstream: ProxyServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keepalive frames do not require an upstream handler to be active."""
    proxy = proxy_without_upstream

    assert proxy.upstream_handler is None
    assert proxy.get_last_data() is None

    await proxy._on_downstream_keepalive(b"hp-keepalive{ka#}")  # type: ignore[reportPrivateUsage]

    assert proxy.upstream_handler is None
    assert proxy.get_last_data() is None


async def test_local_command_still_works_when_upstream_configured_but_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-to-downstream flow remains available when upstream connect fails."""
    with socket.socket() as free_socket:
        free_socket.bind(("127.0.0.1", 0))
        unreachable_port = free_socket.getsockname()[1]

    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host="127.0.0.1",
        upstream_port=unreachable_port,
        upstream_enabled=True,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-local-response{l-upstream-down#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await asyncio.sleep(0.2)

    try:
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-upstream-down#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-upstream-down#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()
        await proxy.stop()


async def test_local_command_still_works_when_upstream_keepalive_forwarding_fails(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-to-downstream flow continues after upstream keepalive forwarding cannot proceed."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-keepalive{ka-fail#}":
            return SimpleNamespace(
                is_keepalive=True,
                message_type=None,
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-after-ka-fail#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()
    await upstream.stop()

    try:
        hp_writer.write(b"hp-keepalive{ka-fail#}")
        await hp_writer.drain()

        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-after-ka-fail#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-after-ka-fail#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_get_last_data_returns_downstream_last_data(
    proxy_without_upstream: ProxyServer,
) -> None:
    """Proxy get_last_data returns the same object stored by downstream handler."""
    proxy = proxy_without_upstream
    expected_data: dict[str, Any] = {
        "timestamp": "2026-06-25T08:00:00",
        "is_old": False,
        "count": 1,
        "groups": [],
        "raw": "{200#}",
    }

    proxy.downstream_handler.last_data = expected_data

    assert proxy.get_last_data() is expected_data
