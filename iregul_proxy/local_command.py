"""Local command socket handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class LocalCommandHandler:
    """Local command socket front-end."""

    LOCAL_COMMAND_MAP = {"502": "200", "501": "10"}

    def __init__(
        self,
        host: str,
        port: int,
        *,
        readuntil_timeout: int,
        file_logger: logging.Logger,
        on_message: Callable[[bytes, str | None], Awaitable[bytes | None]],
    ) -> None:
        self.host = host
        self.port = port
        self.readuntil_timeout = readuntil_timeout
        self.file_logger = file_logger
        self.on_message = on_message
        self.server: asyncio.Server | None = None
        self.active_connections: set[asyncio.Task[None]] = set()
        self._active_client: asyncio.Task[None] | None = None

    def map_external_command(self, external_command: str) -> str:
        """Map local socket command to downstream command type."""
        return self.LOCAL_COMMAND_MAP.get(external_command, external_command)

    @staticmethod
    def build_downstream_request(
        raw_message: str, external_command: str, internal_command: str
    ) -> str:
        """Build transformed command payload sent to downstream heat pump."""
        return raw_message.replace(f"{{{external_command}#}}", f"{{{internal_command}#}}")

    @staticmethod
    def rewrite_response_timestamp(message: str) -> str:
        """Replace all text before first { with current local timestamp."""
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        brace_index = message.find("{")
        if brace_index == -1:
            return f"{timestamp}{message}"
        return f"{timestamp}{message[brace_index:]}"

    async def is_running(self) -> bool:
        """Check if the downstream handler is running."""
        return self.server is not None

    async def start(self) -> asyncio.Server:
        """Start local command socket server."""
        self.server = await asyncio.start_server(self._handle_client_wrapper, self.host, self.port)
        return self.server

    async def stop(self) -> None:
        """Stop local command socket server and active connections."""
        if self.active_connections:
            logger.info(
                "Cancelling %s local command connections...",
                len(self.active_connections),
            )
            for task in self.active_connections:
                task.cancel()
            await asyncio.gather(*self.active_connections, return_exceptions=True)
            logger.info("All local command connections closed")

        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def forward(self, data: bytes) -> None:
        """No-op forward for API symmetry with other handlers."""
        _ = data

    def _handle_client_wrapper(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler to track active local socket tasks."""
        if self._active_client is not None and not self._active_client.done():
            logger.warning("Rejecting local command connection: another client is active")
            task = asyncio.create_task(self._close_rejected_client(writer))
            self.active_connections.add(task)
            task.add_done_callback(self.active_connections.discard)
            return

        task = asyncio.create_task(self.handle_client(reader, writer))
        self._active_client = task
        self.active_connections.add(task)
        task.add_done_callback(self.active_connections.discard)

    async def _close_rejected_client(self, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    @staticmethod
    def extract_local_command(raw_message: str) -> str | None:
        """Extract command from a local command frame like cdraminfo...{502#}."""
        start = raw_message.find("{")
        end = raw_message.find("#}", start + 1)
        if start == -1 or end == -1:
            return None
        command = raw_message[start + 1 : end].strip()
        return command or None

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle local command requests and route to proxy command flow."""
        addr = writer.get_extra_info("peername")
        logger.info("Local command connection from %s", addr)

        try:
            try:
                async with asyncio.timeout(self.readuntil_timeout):
                    raw_command_data = await reader.readuntil(b"}")
            except asyncio.IncompleteReadError as exc:
                raw_command_data = exc.partial

            if not raw_command_data:
                return

            raw_message = raw_command_data.decode("utf-8", errors="ignore").strip()
            if not raw_message:
                return

            response = await self.execute_command(raw_message)

            writer.write(response.encode("utf-8"))
            await writer.drain()

        except TimeoutError:
            logger.error("Timeout reading local command from socket")
            writer.write(b"Local command timeout")
            await writer.drain()
        except Exception as exc:
            logger.error("Error handling local command connection %s: %s", addr, exc)
        finally:
            if self._active_client is asyncio.current_task():
                self._active_client = None
            logger.debug("Closing local command connection from %s", addr)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def execute_command(self, raw_message: str) -> str:
        """Execute a local command and return the response text."""

        external_command = self.extract_local_command(raw_message)
        if external_command is None:
            return "Invalid local command format"

        internal_command = self.map_external_command(external_command)
        request_message = self.build_downstream_request(
            raw_message, external_command, internal_command
        )

        logger.debug("Executing command %s, mapped to %s", external_command, internal_command)

        if request_message == raw_message:
            self.file_logger.debug(raw_message, extra={"source": "LOCAL"})
        else:
            self.file_logger.debug(f"{request_message} ({raw_message})", extra={"source": "LOCAL"})

        try:
            response = await self.on_message(request_message.encode("utf-8"), internal_command)

            if response is not None:
                response_text = response.decode("utf-8", errors="ignore")
                modified_response = self.rewrite_response_timestamp(response_text)

                return modified_response

            return "No response from downstream heat pump"
        except ValueError as exc:
            logger.error("Error executing local command: %s", exc)
            return str(exc)
        except TimeoutError:
            logger.error("Timeout waiting for downstream local command response")
            return "Local command timeout"
