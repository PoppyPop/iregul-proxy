"""Proxy server that receives heat pump data and forwards it upstream."""

import asyncio
import logging
import os
import time
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any

from aioiregul.v2 import decoder


class Direction(Enum):
    """Direction of data flow through the proxy."""

    CLIENT_TO_UPSTREAM = "client->upstream"
    UPSTREAM_TO_CLIENT = "upstream->client"


logger = logging.getLogger(__name__)


class LocalizedFormatter(logging.Formatter):
    """Formatter that uses localized time instead of UTC."""

    converter = time.localtime

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
        *,
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
        self.log_downstream = log_downstream
        self.readuntil_timeout = readuntil_timeout
        self.server: asyncio.Server | None = None
        self.last_data: dict[str, Any] | None = None
        self.last_raw_message: str | None = None
        self.active_connections: set[asyncio.Task[None]] = set()
        self._shutdown_event = asyncio.Event()

        # Set up file logger for both upstream and downstream messages
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

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection.

        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        addr = writer.get_extra_info("peername")
        logger.info(f"Connection from {addr}")

        upstream_writer = None

        try:
            # Connect to upstream server
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host, self.upstream_port
            )
            logger.info(f"Connected to upstream server {self.upstream_host}:{self.upstream_port}")

            # Create tasks for bidirectional forwarding
            client_to_upstream = asyncio.create_task(
                self._forward_data(reader, upstream_writer, Direction.CLIENT_TO_UPSTREAM)
            )
            upstream_to_client = asyncio.create_task(
                self._forward_data(upstream_reader, writer, Direction.UPSTREAM_TO_CLIENT)
            )

            # Wait for either task to complete, then cancel the other
            # This ensures that when one direction closes, the other is immediately terminated
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Check if any task completed with an error (not cancelled)
            for task in done:
                try:
                    # This will raise any exception that occurred in the task
                    task.result()
                    # Task completed normally (connection closed)
                    if task == client_to_upstream:
                        logger.info(f"Client {addr} closed connection")
                    else:
                        logger.info(f"Upstream closed connection for client {addr}")
                except asyncio.CancelledError:
                    pass  # Task was cancelled, ignore
                except Exception as e:
                    if task == client_to_upstream:
                        logger.error(f"Error in client->upstream forwarding for {addr}: {e}")
                    else:
                        logger.error(f"Error in upstream->client forwarding for {addr}: {e}")

            # Cancel any remaining tasks
            for task in pending:
                task.cancel()

            # Wait for cancelled tasks to complete
            await asyncio.gather(*pending, return_exceptions=True)

        except asyncio.CancelledError:
            logger.info(f"Connection from {addr} cancelled during shutdown")
            raise
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            logger.info(f"Closing connection from {addr}")
            try:
                if upstream_writer:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
            except Exception:
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _forward_data(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, direction: Direction
    ):
        """Forward data between reader and writer.

        Args:
            reader: Stream reader to read data from
            writer: Stream writer to write data to
            direction: Direction of data flow
        """
        try:
            while True:
                try:
                    # Read until we find a complete message (ending with })
                    async with asyncio.timeout(self.readuntil_timeout):
                        data = await reader.readuntil(b"}")
                except TimeoutError:
                    logger.error(
                        f"Timeout waiting for message ({direction.value}) - no }} received within {self.readuntil_timeout}s"
                    )
                    continue
                except asyncio.IncompleteReadError as e:
                    # Connection closed without finding }
                    data = e.partial
                    if data:
                        logger.debug(
                            f"Connection closing ({direction.value}), received partial data: {len(data)} bytes"
                        )
                    else:
                        logger.debug(f"Connection closed ({direction.value})")

                if not data:
                    # EOF reached, connection closed
                    logger.debug(f"EOF reached ({direction.value})")
                    break

                # Log and decode if data is coming from client (heat pump)
                if direction == Direction.CLIENT_TO_UPSTREAM:
                    try:
                        text_data = data.decode("utf-8", errors="ignore")
                        self.last_raw_message = text_data
                        if self.log_downstream:
                            logger.debug(f"Received from client: {text_data[:100]}")
                            self.file_logger.debug(text_data, extra={"source": "DOWNSTREAM"})

                        # Try to decode the message
                        try:
                            decoded = await decoder.decode_text(text_data)
                            if not decoded.is_keepalive:
                                self.last_data = {
                                    "timestamp": decoded.timestamp.isoformat()
                                    if decoded.timestamp
                                    else None,
                                    "is_old": decoded.is_old,
                                    "count": decoded.count,
                                    "groups": decoded.groups,
                                    "raw": text_data,
                                }
                            logger.info(
                                f"Successfully decoded frame: timestamp={decoded.timestamp}, groups={len(decoded.groups)}"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to decode message: {e}")
                    except Exception as e:
                        logger.debug(f"Failed to process downstream data: {e}")

                # Log messages from upstream
                if direction == Direction.UPSTREAM_TO_CLIENT:
                    try:
                        text_data = data.decode("utf-8", errors="ignore")
                        logger.debug(f"Received from upstream: {text_data[:100]}")
                        self.file_logger.debug(text_data, extra={"source": "UPSTREAM"})
                    except Exception as e:
                        logger.debug(f"Failed to process upstream data: {e}")

                # Forward the data
                try:
                    writer.write(data)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError) as e:
                    logger.debug(f"Connection broken while forwarding ({direction.value}): {e}")
                    break

        except asyncio.CancelledError:
            logger.debug(f"Forward task cancelled: {direction.value}")
            raise
        except Exception as e:
            logger.error(f"Error forwarding data ({direction.value}): {e}")
            raise
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start(self):
        """Start the proxy server."""
        self._shutdown_event.clear()
        self.server = await asyncio.start_server(
            self._handle_client_wrapper, self.proxy_host, self.proxy_port
        )

        addr = self.server.sockets[0].getsockname()
        logger.info(f"Proxy server started on {addr[0]}:{addr[1]}")
        logger.info(f"Forwarding to {self.upstream_host}:{self.upstream_port}")

    def _handle_client_wrapper(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Wrapper to track client connections.

        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        task = asyncio.create_task(self.handle_client(reader, writer))
        self.active_connections.add(task)
        task.add_done_callback(self.active_connections.discard)

    async def stop(self):
        """Stop the proxy server and close all connections."""
        logger.info("Stopping proxy server...")
        self._shutdown_event.set()

        # Cancel all active connections
        if self.active_connections:
            logger.info(f"Cancelling {len(self.active_connections)} active connections...")
            for task in self.active_connections:
                task.cancel()

            # Wait for all connections to be closed
            await asyncio.gather(*self.active_connections, return_exceptions=True)
            logger.info("All connections closed")

        # Close the server (stop accepting new connections)
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Proxy server stopped accepting new connections")

        logger.info("Proxy server stopped")

    async def serve_forever(self):
        """Serve the proxy server forever or until cancelled."""
        if not self.server:
            raise RuntimeError("Server not started. Call start() first.")

        try:
            # Start serving
            async with self.server:
                # Wait until shutdown is requested
                await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Proxy serve_forever cancelled")
            raise

    def get_last_data(self) -> dict[str, Any] | None:
        """Get the last decoded data received from the heat pump.

        Returns:
            Dictionary with last decoded data or None if no data received yet
        """
        return self.last_data
