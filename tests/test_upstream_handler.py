"""Tests for upstream relay management handler."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from iregul_proxy.upstream import UpstreamConnectionHandler


class FakeWriter:
    """Minimal stream-writer test double."""

    def __init__(self, *, fail_on_drain: bool = False) -> None:
        self._closing = False
        self.fail_on_drain = fail_on_drain
        self.written: list[bytes] = []
        self.closed = False

    def is_closing(self) -> bool:
        return self._closing

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        if self.fail_on_drain:
            raise RuntimeError("drain failed")

    def close(self) -> None:
        self._closing = True
        self.closed = True

    async def wait_closed(self) -> None:
        return


def build_handler(
    on_message: Callable[[bytes, str | None], Awaitable[bytes | None]],
) -> UpstreamConnectionHandler:
    """Build handler with stable localhost values for tests."""
    return UpstreamConnectionHandler(
        host="127.0.0.1",
        port=65003,
        file_logger=logging.getLogger("tests.upstream"),
        on_message=on_message,
    )


def test_extract_command_from_frame_returns_command_for_valid_frame() -> None:
    """Frame parser extracts command for matching iRegul format."""
    assert UpstreamConnectionHandler.extract_command_from_frame("prefix {200#} suffix") == "200"


def test_extract_command_from_frame_returns_none_for_invalid_frame() -> None:
    """Frame parser returns None when delimiters are not present."""
    assert UpstreamConnectionHandler.extract_command_from_frame("prefix {200}") is None


async def test_forward_writes_data_when_connected() -> None:
    """Forward sends payload through active upstream writer."""

    async def on_message(_data: bytes, _expected: str | None) -> bytes | None:
        return None

    handler = build_handler(on_message)
    writer = FakeWriter()
    handler._writer = writer  # type: ignore[reportPrivateUsage]

    await handler.forward(b"{200#}")

    assert writer.written == [b"{200#}"]


async def test_send_to_upstream_closes_writer_on_write_error() -> None:
    """Write errors trigger writer cleanup so future attempts can reconnect."""

    async def on_message(_data: bytes, _expected: str | None) -> bytes | None:
        return None

    handler = build_handler(on_message)
    writer = FakeWriter(fail_on_drain=True)
    handler._writer = writer  # type: ignore[reportPrivateUsage]

    await handler._send_to_upstream(b"{200#}")  # type: ignore[reportPrivateUsage]

    assert writer.closed is True
    assert handler._writer is None  # type: ignore[reportPrivateUsage]


async def test_read_upstream_loop_routes_request_and_writes_response() -> None:
    """Read loop forwards command with extracted type and relays callback response."""
    calls: list[tuple[bytes, str | None]] = []

    async def on_message(data: bytes, expected: str | None) -> bytes | None:
        calls.append((data, expected))
        return b"{reply#}"

    handler = build_handler(on_message)
    writer = FakeWriter()
    handler._writer = writer  # type: ignore[reportPrivateUsage]
    handler._enabled = True  # type: ignore[reportPrivateUsage]

    reader = asyncio.StreamReader()
    reader.feed_data(b"{200#}")
    reader.feed_eof()

    await handler._read_upstream_loop(reader)  # type: ignore[reportPrivateUsage]

    assert calls == [(b"{200#}", "200")]
    assert writer.written == [b"{reply#}"]


async def test_start_and_stop_manage_background_task() -> None:
    """Start creates one task and stop cancels/cleans it."""

    async def on_message(_data: bytes, _expected: str | None) -> bytes | None:
        return None

    handler = build_handler(on_message)

    run_started = asyncio.Event()

    async def fake_run() -> None:
        run_started.set()
        while True:
            await asyncio.sleep(0.1)

    handler._run = fake_run  # type: ignore[method-assign,reportPrivateUsage]

    handler.start()
    await asyncio.wait_for(run_started.wait(), timeout=1.0)

    first_task = handler._task  # type: ignore[reportPrivateUsage]
    assert first_task is not None

    # A second start should not create a new task while one already exists.
    handler.start()
    assert handler._task is first_task  # type: ignore[reportPrivateUsage]

    await handler.stop()
    assert handler._task is None  # type: ignore[reportPrivateUsage]
