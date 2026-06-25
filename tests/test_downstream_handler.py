"""Unit tests for DownstreamConnectionHandler."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from aioiregul.v2 import decoder

from iregul_proxy.downstream import DownstreamConnectionHandler, PendingResponse


class FakeWriter:
    """Minimal stream writer fake for downstream handler tests."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._closing = False
        self.closed = False

    def get_extra_info(self, _name: str):
        return ("127.0.0.1", 65001)

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True
        self.closed = True

    async def wait_closed(self) -> None:
        return


def build_handler() -> tuple[DownstreamConnectionHandler, list[bytes], list[None]]:
    """Create a handler and callback capture lists."""
    keepalive_payloads: list[bytes] = []
    connects: list[None] = []

    async def on_client_connect() -> None:
        connects.append(None)

    async def on_keepalive(payload: bytes) -> None:
        keepalive_payloads.append(payload)

    handler = DownstreamConnectionHandler(
        host="127.0.0.1",
        port=65001,
        readuntil_timeout=1,
        log_downstream=True,
        file_logger=logging.getLogger("tests.downstream"),
        on_client_connect=on_client_connect,
        on_keepalive=on_keepalive,
    )
    return handler, keepalive_payloads, connects


async def test_send_raw_raises_without_active_connection() -> None:
    """Raw send fails when no downstream writer is available."""
    handler, _, _ = build_handler()

    with pytest.raises(ValueError, match="No downstream connection available"):
        await handler._send_raw(b"{200#}")  # type: ignore[reportPrivateUsage]


async def test_forward_raises_without_connection() -> None:
    """Forward path requires an active downstream connection."""
    handler, _, _ = build_handler()

    with pytest.raises(ValueError, match="No downstream connection available"):
        await handler.forward(b"{200#}", expected_message_type="200", source="TEST")


async def test_forward_writes_request_and_returns_matching_response() -> None:
    """Forward writes payload and resolves once a matching frame arrives."""
    handler, _, _ = build_handler()
    writer = FakeWriter()
    handler._writer = writer  # type: ignore[reportPrivateUsage]

    forward_task = asyncio.create_task(
        handler.forward(b"{200#}", expected_message_type="200", source="LOCAL/API")
    )
    await asyncio.sleep(0)

    handler._notify_if_matches("200", b"downstream-prefix{reply#}")  # type: ignore[reportPrivateUsage]
    result = await forward_task

    assert writer.written == [b"{200#}"]
    assert result == b"downstream-prefix{reply#}"


async def test_notify_if_matches_sets_pending_future_result() -> None:
    """Matching message type resolves pending response future."""
    handler, _, _ = build_handler()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    handler._pending_response = PendingResponse(  # type: ignore[reportPrivateUsage]
        expected_message_type="10",
        future=future,
        source="UPSTREAM",
    )

    handler._notify_if_matches("10", b"{10#}")  # type: ignore[reportPrivateUsage]

    assert future.done() is True
    assert future.result() == b"{10#}"


async def test_close_active_connection_closes_writer_and_fails_pending() -> None:
    """Closing active connection should fail pending waits and reset state."""
    handler, _, _ = build_handler()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    writer = FakeWriter()

    handler._writer = writer  # type: ignore[reportPrivateUsage]
    handler._reader = asyncio.StreamReader()  # type: ignore[reportPrivateUsage]
    handler._pending_response = PendingResponse(  # type: ignore[reportPrivateUsage]
        expected_message_type="200",
        future=future,
        source="LOCAL/API",
    )

    await handler._close_active_connection()  # type: ignore[reportPrivateUsage]

    assert writer.closed is True
    assert handler._writer is None  # type: ignore[reportPrivateUsage]
    assert handler._reader is None  # type: ignore[reportPrivateUsage]
    assert future.done() is True
    with pytest.raises(ConnectionError, match="Downstream connection closed"):
        future.result()


async def test_handle_client_rejects_second_downstream_client() -> None:
    """Second client is rejected when one downstream connection is already active."""
    handler, _, connects = build_handler()

    handler._writer = FakeWriter()  # type: ignore[reportPrivateUsage]
    new_writer = FakeWriter()

    await handler.handle_client(asyncio.StreamReader(), new_writer)

    assert new_writer.closed is True
    assert connects == []


async def test_handle_downstream_frame_routes_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keepalive frames are forwarded to keepalive callback."""
    handler, keepalive_payloads, _ = build_handler()

    async def fake_decode_text(_text_data: str) -> SimpleNamespace:
        return SimpleNamespace(
            is_keepalive=True,
            message_type=None,
            timestamp=None,
            groups=[],
        )

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    reader = asyncio.StreamReader()
    reader.feed_data(b"{keepalive#}")
    reader.feed_eof()
    handler._reader = reader  # type: ignore[reportPrivateUsage]
    handler._writer = FakeWriter()  # type: ignore[reportPrivateUsage]

    await handler.handle_downstream_frame()

    assert keepalive_payloads == [b"{keepalive#}"]


async def test_handle_downstream_frame_notifies_pending_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decoded message type matching pending request resolves pending future."""
    handler, _, _ = build_handler()

    async def fake_decode_text(_text_data: str) -> SimpleNamespace:
        return SimpleNamespace(
            is_keepalive=False,
            message_type="200",
            timestamp=None,
            groups=[],
        )

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    handler._pending_response = PendingResponse(  # type: ignore[reportPrivateUsage]
        expected_message_type="200",
        future=future,
        source="LOCAL/API",
    )

    reader = asyncio.StreamReader()
    reader.feed_data(b"{200#}")
    reader.feed_eof()
    handler._reader = reader  # type: ignore[reportPrivateUsage]
    handler._writer = FakeWriter()  # type: ignore[reportPrivateUsage]

    await handler.handle_downstream_frame()

    assert future.done() is True
    assert future.result() == b"{200#}"
