"""Proxy server that receives heat pump data and forwards it upstream."""

from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

from .downstream import DownstreamConnectionHandler
from .local_command import LocalCommandHandler
from .upstream import UpstreamConnectionHandler

logger = logging.getLogger(__name__)


class LocalizedFormatter(logging.Formatter):
    """Formatter that uses localized time instead of UTC."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with source field.

        Args:
            record: The log record to format

        Returns:
            Formatted log string
        """
        if not hasattr(record, "source"):
            record.source = "UNKNOWN"
        return super().format(record)


class ProxyServer:
    """Proxy server for iRegul heat pump communication."""

    def __init__(
        self,
        proxy_host: str,
        proxy_port: int,
        upstream_host: str,
        upstream_port: int,
        local_command_host: str,
        local_command_port: int,
        *,
        upstream_enabled: bool = True,
        log_downstream: bool,
        log_dir: str,
        log_max_bytes: int,
        log_backup_count: int,
        readuntil_timeout: int,
    ):
        """Initialize the proxy server.

        Args:
            proxy_host: Host to bind the proxy server to
            proxy_port: Port to bind the proxy server to
            upstream_host: Upstream server host to forward messages to
            upstream_port: Upstream server port to forward messages to
            local_command_host: Host to bind local command socket server to
            local_command_port: Port to bind local command socket server to
            log_downstream: Whether to log messages from downstream (client/heat pump)
            log_dir: Directory for log files
            log_max_bytes: Maximum size of each log file before rotation (default 10 MB)
            log_backup_count: Number of rotated log files to retain (default 8)
            readuntil_timeout: Timeout in seconds for reading messages ending with } (default 5s)
        """
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.upstream_enabled = upstream_enabled
        self.local_command_host = local_command_host
        self.local_command_port = local_command_port
        self.upstream_handler: UpstreamConnectionHandler | None = None
        self._shutdown_event = asyncio.Event()

        os.makedirs(log_dir, exist_ok=True)
        log_format = LocalizedFormatter("%(asctime)s - [%(source)s] - %(message)s")

        self.file_logger = logging.getLogger("iregul_proxy.messages")
        self.file_logger.setLevel(logging.DEBUG)
        self.file_logger.propagate = False
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "messages.log"),
            maxBytes=log_max_bytes,
            backupCount=log_backup_count,
        )
        file_handler.setFormatter(log_format)
        self.file_logger.addHandler(file_handler)

        if self.upstream_enabled:
            self.upstream_handler = UpstreamConnectionHandler(
                host=self.upstream_host,
                port=self.upstream_port,
                file_logger=self.file_logger,
                on_message=self._on_upstream_message,
            )

        self.downstream_handler = DownstreamConnectionHandler(
            host=self.proxy_host,
            port=self.proxy_port,
            readuntil_timeout=readuntil_timeout,
            log_downstream=log_downstream,
            file_logger=self.file_logger,
            on_client_connect=self._on_downstream_client_connect,
            on_keepalive=self._on_downstream_keepalive,
        )

        self.local_command_handler = LocalCommandHandler(
            host=self.local_command_host,
            port=self.local_command_port,
            readuntil_timeout=readuntil_timeout,
            file_logger=self.file_logger,
            on_message=self._on_local_command_message,
        )

    async def _on_downstream_client_connect(self) -> None:
        if self.upstream_handler is not None:
            self.upstream_handler.start()

    async def _on_downstream_keepalive(self, payload: bytes) -> None:
        if self.upstream_handler is not None:
            await self.upstream_handler.forward(payload)

    async def _on_upstream_message(
        self,
        payload: bytes,
        expected_type: str | None,
    ) -> bytes | None:
        return await self._on_message_to_forward(payload, expected_type, source="UPS")

    async def _on_message_to_forward(
        self,
        payload: bytes,
        expected_type: str | None,
        source: str,
    ) -> bytes | None:
        if expected_type is None:
            logger.warning(f"{source} message has no expected type, cannot forward to downstream")
            return None

        try:
            return await self.downstream_handler.forward(
                payload=payload,
                expected_message_type=expected_type,
                source=source,
            )
        except (TimeoutError, ConnectionError, ValueError) as exc:
            logger.warning(f"{source} transfer failed: {exc}")
            return None

    async def _on_local_command_message(
        self,
        payload: bytes,
        expected_type: str | None,
    ) -> bytes | None:
        return await self._on_message_to_forward(payload, expected_type, source="LOC")

    async def start(self):
        """Start the proxy server."""
        self._shutdown_event.clear()

        server = await self.downstream_handler.start()
        addr = server.sockets[0].getsockname()
        logger.info("Proxy server started on %s:%s", addr[0], addr[1])

        if self.upstream_handler is not None:
            logger.info("Registering forwarding to %s:%s", self.upstream_host, self.upstream_port)
        else:
            logger.info("Upstream forwarding disabled")

        local_command_server = await self.local_command_handler.start()
        local_addr = local_command_server.sockets[0].getsockname()
        logger.info("Local command socket started on %s:%s", local_addr[0], local_addr[1])

    async def stop(self):
        """Stop the proxy server and close all connections."""
        logger.info("Stopping proxy server...")
        self._shutdown_event.set()

        if self.downstream_handler:
            await self.downstream_handler.stop()
            logger.info("Proxy server stopped accepting new connections")

        if self.upstream_handler is not None:
            await self.upstream_handler.stop()
            logger.info("Upstream handler stopped accepting new connections")

        if self.local_command_handler:
            await self.local_command_handler.stop()
            logger.info("Local command server stopped accepting new connections")

        logger.info("Proxy server stopped")

    async def serve_forever(self):
        """Serve the proxy server forever or until cancelled."""
        if (
            not await self.downstream_handler.is_running()
            or not await self.local_command_handler.is_running()
        ):
            raise RuntimeError("Server not started. Call start() first.")

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Proxy serve_forever cancelled")
            raise

    def get_last_data(self) -> dict[str, Any] | None:
        """Get the last decoded data received from the heat pump.

        Returns:
            Dictionary with last decoded data or None if no data received yet
        """
        return self.downstream_handler.last_data
