"""Downstream connection/session handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aioiregul.v2 import decoder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class PendingResponse:
    """Pending response waiting for a matching downstream frame."""

    expected_message_type: str
    future: asyncio.Future[bytes]
    source: str


class DownstreamConnectionHandler:
    """Owns downstream client connection lifecycle for the proxy."""

    KNOWN_MESSAGE_TYPES = {"10", "200"}

    def __init__(
        self,
        host: str,
        port: int,
        *,
        readuntil_timeout: int,
        log_downstream: bool,
        file_logger: logging.Logger,
        on_client_connect: Callable[[], Awaitable[None]],
        on_keepalive: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self.host = host
        self.port = port
        self.readuntil_timeout = readuntil_timeout
        self.log_downstream = log_downstream
        self.file_logger = file_logger
        self.on_client_connect = on_client_connect
        self.on_keepalive = on_keepalive
        self.server: asyncio.Server | None = None
        self.active_connections: set[asyncio.Task[None]] = set()
        self._connection_lock = asyncio.Lock()
        self._read_write_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending_response: PendingResponse | None = None
        self.last_data: dict[str, Any] | None = None

    async def start(self) -> asyncio.Server:
        """Start downstream proxy listener."""
        self.server = await asyncio.start_server(
            self._handle_client_wrapper,
            self.host,
            self.port,
            limit=1024 * 1024,
        )
        return self.server

    async def is_running(self) -> bool:
        """Check if the downstream handler is running."""
        return self.server is not None

    async def stop(self) -> None:
        """Stop downstream listener and cancel active downstream handlers."""
        if self.active_connections:
            logger.info("Cancelling %s active connections...", len(self.active_connections))
            for task in self.active_connections:
                task.cancel()
            await asyncio.gather(*self.active_connections, return_exceptions=True)
            logger.info("All connections closed")

        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        await self._close_active_connection()

    async def forward(
        self,
        payload: bytes,
        *,
        expected_message_type: str,
        source: str,
    ) -> bytes:
        """Send one request to downstream and wait for matching response."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()

        async with self._request_lock:
            if not await self.is_connected():
                raise ValueError("No downstream connection available")

            self._pending_response = PendingResponse(
                expected_message_type=expected_message_type,
                future=future,
                source=source,
            )

            try:
                await self._send_raw(payload)
                timeout_seconds = float(self.readuntil_timeout)
                return await asyncio.wait_for(future, timeout=timeout_seconds)
            finally:
                self._pending_response = None

    async def is_connected(self) -> bool:
        async with self._connection_lock:
            return self._writer is not None and not self._writer.is_closing()

    async def _send_raw(self, data: bytes) -> None:
        async with self._connection_lock:
            writer = self._writer

        if writer is None or writer.is_closing():
            raise ValueError("No downstream connection available")

        async with self._read_write_lock:
            writer.write(data)
            await writer.drain()

    def _notify_if_matches(self, message_type: str, payload: bytes) -> str:
        pending = self._pending_response
        if (
            pending is not None
            and not pending.future.done()
            and pending.expected_message_type == message_type
        ):
            pending.future.set_result(payload)
            logger.debug("Notified pending downstream callback for message type %s", message_type)
            return pending.source
        return "UNKNOWN"

    async def _close_active_connection(self) -> None:
        async with self._connection_lock:
            writer = self._writer
            self._reader = None
            self._writer = None

        pending = self._pending_response
        if pending is not None and not pending.future.done():
            pending.future.set_exception(ConnectionError("Downstream connection closed"))
            self._pending_response = None

        if writer is None:
            return

        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    def _handle_client_wrapper(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler to track active downstream tasks."""
        task = asyncio.create_task(self.handle_client(reader, writer))
        self.active_connections.add(task)
        task.add_done_callback(self.active_connections.discard)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle one downstream heat pump connection."""
        addr = writer.get_extra_info("peername")

        if await self.is_connected():
            logger.warning("Rejecting downstream client %s: another client is active", addr)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            return

        logger.info("Connection from %s", addr)
        async with self._connection_lock:
            self._reader = reader
            self._writer = writer

        await self.on_client_connect()

        try:
            await self.handle_downstream_frame()
        except asyncio.CancelledError:
            logger.info("Connection from %s cancelled during shutdown", addr)
            raise
        except Exception as exc:
            logger.error("Error handling client %s: %s", addr, exc)
        finally:
            await self._close_active_connection()
            logger.info("Closing connection from %s", addr)

    async def handle_downstream_frame(self) -> None:
        """Read and process downstream frames from active connection."""
        while await self.is_connected():
            should_log = True
            decoded: Any | None = None
            async with self._connection_lock:
                reader = self._reader

            if reader is None:
                break

            try:
                async with asyncio.timeout(self.readuntil_timeout):
                    data = await reader.readuntil(b"}")
            except TimeoutError:
                logger.error(
                    "Timeout waiting for downstream message - no } received within %ss",
                    self.readuntil_timeout,
                )
                continue
            except asyncio.IncompleteReadError as exc:
                data = exc.partial
                if not data:
                    break

            if not data:
                break

            text_data = data.decode("utf-8", errors="ignore")

            try:
                decoded = await decoder.decode_text(text_data)

                if decoded.is_keepalive:
                    should_log = False
                    await self.on_keepalive(data)
                    continue
                elif decoded.message_type in self.KNOWN_MESSAGE_TYPES:
                    should_log = self.log_downstream

                logger.info(
                    "Successfully decoded frame: timestamp=%s, groups=%s",
                    decoded.timestamp,
                    len(decoded.groups),
                )

                self.last_data = {
                    "timestamp": decoded.timestamp.isoformat() if decoded.timestamp else None,
                    "is_old": decoded.is_old,
                    "count": decoded.count,
                    "groups": decoded.groups,
                    "raw": text_data,
                }
            except Exception as exc:
                logger.warning("Failed to decode message: %s. Message: %s", exc, text_data[:200])

            request_source = "UNKNOWN"
            if decoded is not None and decoded.message_type is not None:
                request_source = self._notify_if_matches(decoded.message_type, data)

            logger.debug("Received from client: %s", text_data[:100])
            self.file_logger.debug(
                text_data if should_log else "LOGGING DISABLED",
                extra={"source": "DOWN-" + request_source},
            )
