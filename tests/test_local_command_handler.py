"""Unit tests for LocalCommandHandler."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from collections.abc import Awaitable, Callable
from typing import Any
from pytest import MonkeyPatch

from iregul_proxy.local_command import LocalCommandHandler


class FakeWriter:
    """Minimal stream writer fake for LocalCommandHandler tests."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def get_extra_info(self, _name: str) -> Any:
        return ("127.0.0.1", 12345)

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return


class TimeoutReader:
    """Reader fake that immediately raises TimeoutError on readuntil."""

    async def readuntil(self, _separator: bytes) -> bytes:
        raise TimeoutError


def build_handler(
    on_message: Callable[[bytes, str | None], Awaitable[bytes | None]],
    *,
    readuntil_timeout: int = 1,
) -> LocalCommandHandler:
    """Build LocalCommandHandler with stable test defaults."""
    return LocalCommandHandler(
        host="127.0.0.1",
        port=65011,
        readuntil_timeout=readuntil_timeout,
        file_logger=logging.getLogger("tests.local_command"),
        on_message=on_message,
    )


def test_map_external_command_uses_known_mapping() -> None:
    """Known local commands are mapped to downstream message types."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        return b"unused"

    handler = build_handler(on_message)
    assert handler.map_external_command("502") == "200"
    assert handler.map_external_command("501") == "10"


def test_map_external_command_returns_original_for_unknown() -> None:
    """Unknown local command should pass through unchanged."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        return b"unused"

    handler = build_handler(on_message)
    assert handler.map_external_command("777") == "777"


def test_build_downstream_request_formats_frame() -> None:
    """Downstream request frame format is preserved."""
    assert LocalCommandHandler.build_downstream_request("{10#}","10","200") == "{200#}"


def test_extract_local_command_handles_valid_and_invalid_frames() -> None:
    """Extract command from valid frame and return None for invalid text."""
    assert LocalCommandHandler.extract_local_command("cdraminfoDEV1PWD1{502#}") == "502"
    assert LocalCommandHandler.extract_local_command("no-frame") is None


def test_rewrite_response_timestamp_replaces_prefix_before_first_brace() -> None:
    """Timestamp rewrite keeps only message payload starting at first brace."""
    rewritten = LocalCommandHandler.rewrite_response_timestamp("prefix{reply#}")
    assert re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{reply#\}$", rewritten)


async def test_handle_client_sends_response_from_on_message(monkeypatch: MonkeyPatch) -> None:
    """Valid local command is routed to callback and response is written back."""

    class FixedDateTime(datetime):
        @classmethod
        def now(cls) -> datetime:
            return cls(2026, 6, 26, 12, 0, 0)

    async def on_message(payload: bytes, command: str | None) -> bytes:
        assert command == "200"
        return b"cdraminfo{reply#}"

    handler = build_handler(on_message)
    from iregul_proxy import local_command

    monkeypatch.setattr(local_command, "datetime", FixedDateTime)
    reader = asyncio.StreamReader()
    reader.feed_data(b"cdraminfoDEV1PWD1{502#}")
    reader.feed_eof()
    writer = FakeWriter()

    await handler.handle_client(reader, writer)  # type: ignore[arg-type]

    assert bytes(writer.buffer) == b"26/06/2026 12:00:00{reply#}"
    assert writer.closed is True


async def test_handle_client_writes_error_on_invalid_command_format() -> None:
    """Invalid local command payload returns explicit format error."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        return b"should-not-be-called"

    handler = build_handler(on_message)
    reader = asyncio.StreamReader()
    reader.feed_data(b"invalid")
    reader.feed_eof()
    writer = FakeWriter()

    await handler.handle_client(reader, writer)  # type: ignore[arg-type]

    assert bytes(writer.buffer) == b"Invalid local command format"


async def test_handle_client_writes_value_error_message() -> None:
    """ValueError from callback is propagated as socket response text."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        raise ValueError("No downstream connection available")

    handler = build_handler(on_message)
    reader = asyncio.StreamReader()
    reader.feed_data(b"cdraminfoDEV1PWD1{502#}")
    reader.feed_eof()
    writer = FakeWriter()

    await handler.handle_client(reader, writer)  # type: ignore[arg-type]

    assert bytes(writer.buffer) == b"No downstream connection available"


async def test_handle_client_writes_timeout_message_on_read_timeout() -> None:
    """Read timeout returns timeout message to local socket client."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        return b"unused"

    handler = build_handler(on_message)
    writer = FakeWriter()

    await handler.handle_client(TimeoutReader(), writer)  # type: ignore[arg-type]

    assert bytes(writer.buffer) == b"Local command timeout"


async def test_handle_client_wrapper_rejects_second_active_client() -> None:
    """Wrapper should reject second client while one local client is active."""

    async def on_message(_payload: bytes, _command: str | None) -> bytes | None:
        return b"unused"

    handler = build_handler(on_message)

    blocker = asyncio.create_task(asyncio.sleep(1))
    handler._active_client = blocker  # type: ignore[reportPrivateUsage]

    writer = FakeWriter()
    handler._handle_client_wrapper(asyncio.StreamReader(), writer)  # type: ignore[reportPrivateUsage]

    await asyncio.sleep(0)
    assert writer.closed is True

    blocker.cancel()
    await asyncio.gather(blocker, return_exceptions=True)
