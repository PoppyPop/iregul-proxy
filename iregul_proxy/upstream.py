"""Upstream relay management for one downstream session."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class UpstreamConnectionHandler:
    """Manages one optional upstream connection for the proxy."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        file_logger: logging.Logger,
        on_message: Callable[[bytes, str | None], Awaitable[bytes | None]],
    ) -> None:
        self.host = host
        self.port = port
        self.file_logger = file_logger
        self.on_message = on_message
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._enabled = False

    def start(self) -> None:
        """Start upstream connection task."""
        self._enabled = True
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop upstream connection and clean resources."""
        self._enabled = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

        await self._close_writer()

    async def forward(self, data: bytes) -> None:
        """Forward bytes to upstream if connected."""
        if not self._is_connected():
            return
        await self._send_to_upstream(data)

    async def _run(self) -> None:
        """Maintain upstream connection with reconnect loop."""
        while self._enabled:
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    self.host, self.port
                )
                self._writer = upstream_writer
                logger.info("Connected upstream %s:%s", self.host, self.port)
                await self._read_upstream_loop(upstream_reader)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Upstream must never block downstream, local socket, or API command path.
                logger.warning("Upstream relay unavailable: %s", exc)
            finally:
                await self._close_writer()

            if self._enabled:
                await asyncio.sleep(1.0)

    async def _read_upstream_loop(self, upstream_reader: asyncio.StreamReader) -> None:
        """Read upstream requests and execute them through downstream callback flow."""
        while self._enabled:
            try:
                data = await upstream_reader.readuntil(b"}")
            except asyncio.IncompleteReadError as exc:
                data = exc.partial
                if not data:
                    return

            if not data:
                return

            text_data = data.decode("utf-8", errors="ignore")
            self.file_logger.debug(text_data, extra={"source": "UPSTREAM"})
            logger.debug("Received from upstream: %s", text_data[:100])

            expected_type = self.extract_command_from_frame(text_data)
            response = await self.on_message(data, expected_type)
            if response is not None:
                await self._send_to_upstream(response)

    @staticmethod
    def extract_command_from_frame(raw_message: str) -> str | None:
        """Extract command type from frame like {200#} for response matching."""
        start = raw_message.find("{")
        end = raw_message.find("#", start + 1)
        if start == -1 or end == -1:
            return None
        command = raw_message[start + 1 : end].strip()
        return command or None

    def _is_connected(self) -> bool:
        """Return true when upstream writer is available and open."""
        return self._writer is not None and not self._writer.is_closing()

    async def _send_to_upstream(self, data: bytes) -> None:
        """Safely write bytes to upstream."""
        writer = self._writer
        if writer is None or writer.is_closing():
            return

        try:
            async with self._write_lock:
                writer.write(data)
                await writer.drain()
        except Exception as exc:
            logger.error("Failed writing to upstream: %s", exc)
            await self._close_writer()

    async def _close_writer(self) -> None:
        """Close upstream writer if present."""
        writer = self._writer
        self._writer = None
        if writer is None:
            return
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
