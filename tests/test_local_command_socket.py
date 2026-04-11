"""Tests for local command socket forwarding behavior."""

import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest
from aioiregul.v2 import decoder

from iregul_proxy.proxy import ProxyServer

logger = logging.getLogger(__name__)


def build_decoded_message(message_type: str) -> SimpleNamespace:
    """Create a minimal decoded message object for proxy tests."""
    return SimpleNamespace(
        message_type=message_type,
        is_keepalive=False,
        timestamp=None,
        is_old=False,
        count=0,
        groups=[],
    )


class MockUpstreamServer:
    """Mock upstream server for testing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self.received_messages: list[bytes] = []

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                try:
                    data = await reader.readuntil(b"}")
                except asyncio.IncompleteReadError as e:
                    data = e.partial
                    if not data:
                        break

                if not data:
                    break

                self.received_messages.append(data)
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        if self.port == 0:
            self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def running_proxy() -> Any:
    """Start a proxy server with local command socket enabled."""
    upstream = MockUpstreamServer()
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
        log_max_bytes=10485760,
        log_backup_count=5,
    )
    await proxy.start()

    if proxy.server and proxy.server.sockets:
        proxy.proxy_port = proxy.server.sockets[0].getsockname()[1]
    if proxy.local_command_server and proxy.local_command_server.sockets:
        proxy.local_command_port = proxy.local_command_server.sockets[0].getsockname()[1]

    try:
        yield proxy, upstream
    finally:
        await proxy.stop()
        await upstream.stop()


async def test_local_command_is_mapped_and_response_not_forwarded_upstream(
    running_proxy: tuple[ProxyServer, MockUpstreamServer], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Map command, send to downstream, rewrite response timestamp, and suppress upstream forwarding."""
    proxy, upstream = running_proxy

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "downstream-prefix{reply#}":
            return build_decoded_message("200")
        raise ValueError(f"Unexpected frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    downstream_reader, downstream_writer = await asyncio.open_connection(
        proxy.proxy_host, proxy.proxy_port
    )

    async def downstream_responder() -> bytes:
        request = await downstream_reader.readuntil(b"}")
        downstream_writer.write(b"downstream-prefix{reply#}")
        await downstream_writer.drain()
        return request

    responder_task = asyncio.create_task(downstream_responder())

    local_reader, local_writer = await asyncio.open_connection(
        proxy.local_command_host, proxy.local_command_port
    )
    local_writer.write(b"cdraminfoDEV1PWD1{502#}")
    await local_writer.drain()

    local_response = await local_reader.read(1024)
    local_response_text = local_response.decode("utf-8", errors="ignore")
    downstream_request = (await responder_task).decode("utf-8", errors="ignore")

    assert downstream_request == "{200#}"
    assert re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{reply#\}$", local_response_text)
    assert upstream.received_messages == []

    local_writer.close()
    await local_writer.wait_closed()
    downstream_writer.close()
    await downstream_writer.wait_closed()


async def test_local_command_only_captures_matching_message_type(
    running_proxy: tuple[ProxyServer, MockUpstreamServer], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the requested message type should be returned to the local socket."""
    proxy, upstream = running_proxy

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "downstream-prefix{mismatch#}":
            return build_decoded_message("10")
        if text_data == "downstream-prefix{reply#}":
            return build_decoded_message("200")
        raise ValueError(f"Unexpected frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    downstream_reader, downstream_writer = await asyncio.open_connection(
        proxy.proxy_host, proxy.proxy_port
    )

    async def downstream_responder() -> bytes:
        request = await downstream_reader.readuntil(b"}")
        downstream_writer.write(b"downstream-prefix{mismatch#}")
        await downstream_writer.drain()
        await asyncio.sleep(0.05)
        downstream_writer.write(b"downstream-prefix{reply#}")
        await downstream_writer.drain()
        return request

    responder_task = asyncio.create_task(downstream_responder())

    local_reader, local_writer = await asyncio.open_connection(
        proxy.local_command_host, proxy.local_command_port
    )
    local_writer.write(b"cdraminfoDEV1PWD1{502#}")
    await local_writer.drain()

    local_response = await local_reader.read(1024)
    local_response_text = local_response.decode("utf-8", errors="ignore")
    downstream_request = (await responder_task).decode("utf-8", errors="ignore")

    assert downstream_request == "{200#}"
    assert upstream.received_messages == [b"downstream-prefix{mismatch#}"]
    assert re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{reply#\}$", local_response_text)

    local_writer.close()
    await local_writer.wait_closed()
    downstream_writer.close()
    await downstream_writer.wait_closed()
