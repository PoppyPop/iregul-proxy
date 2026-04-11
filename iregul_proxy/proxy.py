"""Proxy server that receives heat pump data and forwards it upstream."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any

from aioiregul.v2 import decoder


class Direction(Enum):
    """Direction of data flow through the proxy."""

    CLIENT_TO_UPSTREAM = "client->upstream"
    UPSTREAM_TO_CLIENT = "upstream->client"


logger = logging.getLogger(__name__)


@dataclass
class DownstreamConnection:
    """Downstream connection metadata for local command routing."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    write_lock: asyncio.Lock
    pending_local_response: asyncio.Future[bytes] | None = None
    pending_local_response_type: str | None = None


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

    # Known message types that are typically logged only if LOG_DOWNSTREAM is true
    KNOWN_MESSAGE_TYPES = {"10", "200"}
    LOCAL_COMMAND_MAP = {"502": "200", "501": "10"}

    def __init__(
        self,
        proxy_host: str,
        proxy_port: int,
        upstream_host: str,
        upstream_port: int,
        local_command_host: str,
        local_command_port: int,
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
        self.local_command_host = local_command_host
        self.local_command_port = local_command_port
        self.log_downstream = log_downstream
        self.readuntil_timeout = readuntil_timeout
        self.server: asyncio.Server | None = None
        self.local_command_server: asyncio.Server | None = None
        self.last_data: dict[str, Any] | None = None
        self.last_raw_message: str | None = None
        self.active_connections: set[asyncio.Task[None]] = set()
        self.active_local_connections: set[asyncio.Task[None]] = set()
        self._downstream_connections: dict[int, DownstreamConnection] = {}
        self._downstream_connections_lock = asyncio.Lock()
        self._next_connection_id = 0
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

    @staticmethod
    def _replace_prefix_with_timestamp(message: str) -> str:
        """Replace all text before the first '{' with current timestamp."""
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        brace_index = message.find("{")
        if brace_index == -1:
            return f"{timestamp}{message}"
        return f"{timestamp}{message[brace_index:]}"

    def _map_local_command(self, external_command: str) -> str:
        """Map external command to internal command if configured."""
        return self.LOCAL_COMMAND_MAP.get(external_command, external_command)

    @staticmethod
    def _extract_local_command(raw_message: str) -> str | None:
        """Extract command from a local command frame like cdraminfo...{502#}."""
        start = raw_message.find("{")
        end = raw_message.find("#}", start + 1)
        if start == -1 or end == -1:
            return None
        command = raw_message[start + 1 : end].strip()
        return command or None

    @staticmethod
    def _build_local_request(command: str) -> str:
        """Build transformed command payload sent to downstream heat pump."""
        return f"{{{command}#}}"

    async def execute_command(self, external_command: str) -> str:
        """Execute a command on the downstream heat pump.

        Maps the external command, sends it to the active downstream connection,
        waits for the matching response, and returns the timestamped response string.

        Args:
            external_command: External command string (e.g. ``"502"`` or ``"200"``)

        Returns:
            Timestamped response string from the heat pump

        Raises:
            ValueError: If no downstream connection is available or one is already pending
            TimeoutError: If the heat pump does not respond within the configured timeout
            ConnectionError: If the downstream connection is lost while waiting
        """
        internal_command = self._map_local_command(external_command)
        request_message = self._build_local_request(internal_command)
        logger.debug(f"Executing command {external_command}, mapped to {internal_command}")
        self.file_logger.debug(request_message, extra={"source": "LOCAL-UP"})

        _connection_id, downstream = await self._get_latest_downstream_connection()
        if downstream is None:
            raise ValueError("No downstream connection available")

        if (
            downstream.pending_local_response is not None
            and not downstream.pending_local_response.done()
        ):
            raise ValueError("A local command is already pending on downstream connection")

        loop = asyncio.get_running_loop()
        downstream.pending_local_response = loop.create_future()
        downstream.pending_local_response_type = internal_command

        async with downstream.write_lock:
            downstream.writer.write(request_message.encode("utf-8"))
            await downstream.writer.drain()

        try:
            response_data = await asyncio.wait_for(
                downstream.pending_local_response,
                timeout=float(self.readuntil_timeout),
            )
        finally:
            downstream.pending_local_response = None
            downstream.pending_local_response_type = None

        response_text = response_data.decode("utf-8", errors="ignore")
        modified_response = self._replace_prefix_with_timestamp(response_text)
        self.file_logger.debug(modified_response, extra={"source": "LOCAL-DOWN"})
        return modified_response

    async def _register_downstream_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> int:
        """Register a downstream connection for local command routing."""
        async with self._downstream_connections_lock:
            self._next_connection_id += 1
            connection_id = self._next_connection_id
            self._downstream_connections[connection_id] = DownstreamConnection(
                reader=reader,
                writer=writer,
                write_lock=asyncio.Lock(),
            )
            return connection_id

    async def _pop_downstream_connection(self, connection_id: int) -> DownstreamConnection | None:
        """Unregister and return a downstream connection."""
        async with self._downstream_connections_lock:
            return self._downstream_connections.pop(connection_id, None)

    async def _get_latest_downstream_connection(
        self,
    ) -> tuple[int, DownstreamConnection] | tuple[None, None]:
        """Get latest active downstream connection."""
        async with self._downstream_connections_lock:
            if not self._downstream_connections:
                return None, None
            connection_id = max(self._downstream_connections.keys())
            return connection_id, self._downstream_connections[connection_id]

    async def handle_local_command_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle local command socket requests."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Local command connection from {addr}")

        try:
            try:
                async with asyncio.timeout(self.readuntil_timeout):
                    raw_command_data = await reader.readuntil(b"}")
            except asyncio.IncompleteReadError as e:
                raw_command_data = e.partial
            if not raw_command_data:
                return

            raw_message = raw_command_data.decode("utf-8", errors="ignore").strip()
            if not raw_message:
                return

            self.file_logger.debug(raw_message, extra={"source": "LOCAL"})
            external_command = self._extract_local_command(raw_message)
            if external_command is None:
                error = "Invalid local command format"
                writer.write(error.encode("utf-8"))
                await writer.drain()
                return

            try:
                response = await self.execute_command(external_command)
                writer.write(response.encode("utf-8"))
                await writer.drain()
            except ValueError as e:
                writer.write(str(e).encode("utf-8"))
                await writer.drain()
            except TimeoutError:
                logger.error("Timeout waiting for downstream local command response")
                writer.write(b"Local command timeout")
                await writer.drain()
        except TimeoutError:
            logger.error("Timeout reading local command from socket")
            writer.write(b"Local command timeout")
            await writer.drain()
        except Exception as e:
            logger.error(f"Error handling local command connection {addr}: {e}")
        finally:
            logger.info(f"Closing local command connection from {addr}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _handle_local_command_wrapper(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrapper to track local command client connections."""
        task = asyncio.create_task(self.handle_local_command_client(reader, writer))
        self.active_local_connections.add(task)
        task.add_done_callback(self.active_local_connections.discard)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection.

        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        addr = writer.get_extra_info("peername")
        logger.info(f"Connection from {addr}")

        upstream_writer = None
        connection_id = await self._register_downstream_connection(reader, writer)

        try:
            # Connect to upstream server
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host,
                self.upstream_port,
                limit=1024 * 1024,  # 1 MB buffer size for upstream connection
            )
            logger.info(f"Connected to upstream server {self.upstream_host}:{self.upstream_port}")

            # Create tasks for bidirectional forwarding
            client_to_upstream = asyncio.create_task(
                self._forward_data(
                    reader,
                    upstream_writer,
                    Direction.CLIENT_TO_UPSTREAM,
                    connection_id=connection_id,
                )
            )
            upstream_to_client = asyncio.create_task(
                self._forward_data(
                    upstream_reader,
                    writer,
                    Direction.UPSTREAM_TO_CLIENT,
                    connection_id=connection_id,
                )
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
            popped_connection = await self._pop_downstream_connection(connection_id)
            if (
                popped_connection is not None
                and popped_connection.pending_local_response is not None
                and not popped_connection.pending_local_response.done()
            ):
                popped_connection.pending_local_response.set_exception(
                    ConnectionError("Downstream connection closed")
                )
                popped_connection.pending_local_response_type = None
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
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: Direction,
        connection_id: int | None = None,
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
                    # Only apply timeout for downstream (client to upstream) - no keepalive from upstream
                    if direction == Direction.CLIENT_TO_UPSTREAM:
                        async with asyncio.timeout(self.readuntil_timeout):
                            data = await reader.readuntil(b"}")
                    else:
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
                    # Default to logging if we can't decode, to capture unknown formats
                    should_log = True
                    text_data = data.decode("utf-8", errors="ignore")

                    try:
                        self.last_raw_message = text_data

                        # Try to decode the message
                        try:
                            decoded = await decoder.decode_text(text_data)

                            # Determine if we should log this message
                            if decoded.is_keepalive:
                                # Never log keepalive messages
                                should_log = False
                            elif decoded.message_type in self.KNOWN_MESSAGE_TYPES:
                                # Known message types: log only if LOG_DOWNSTREAM is true
                                should_log = self.log_downstream

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

                            if connection_id is not None:
                                downstream = self._downstream_connections.get(connection_id)
                                expected_message_type = (
                                    downstream.pending_local_response_type
                                    if downstream is not None
                                    else None
                                )
                                if (
                                    downstream is not None
                                    and downstream.pending_local_response is not None
                                    and not downstream.pending_local_response.done()
                                    and decoded.message_type == expected_message_type
                                ):
                                    downstream.pending_local_response.set_result(data)
                                    logger.debug(
                                        "Captured downstream response for local command; skipping upstream forwarding"
                                    )
                                    continue  # Skip forwarding this response upstream

                        except Exception as e:
                            logger.warning(
                                f"Failed to decode message: {e}. Message: {text_data[:200]}"
                            )
                    except Exception as e:
                        logger.debug(
                            f"Failed to process downstream data: {e}. Message: {data[:200]}"
                        )

                    if should_log:
                        logger.debug(f"Received from client: {text_data[:100]}")
                        self.file_logger.debug(text_data, extra={"source": "DOWNSTREAM"})

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
                    if direction == Direction.UPSTREAM_TO_CLIENT and connection_id is not None:
                        downstream = self._downstream_connections.get(connection_id)
                        if downstream is not None:
                            async with downstream.write_lock:
                                writer.write(data)
                                await writer.drain()
                        else:
                            writer.write(data)
                            await writer.drain()
                    else:
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
            self._handle_client_wrapper,
            self.proxy_host,
            self.proxy_port,
            limit=1024 * 1024,  # 1 MB buffer size for incoming client connections
        )

        self.local_command_server = await asyncio.start_server(
            self._handle_local_command_wrapper,
            self.local_command_host,
            self.local_command_port,
        )

        addr = self.server.sockets[0].getsockname()
        logger.info(f"Proxy server started on {addr[0]}:{addr[1]}")
        logger.info(f"Forwarding to {self.upstream_host}:{self.upstream_port}")
        local_addr = self.local_command_server.sockets[0].getsockname()
        logger.info(f"Local command socket started on {local_addr[0]}:{local_addr[1]}")

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

        if self.active_local_connections:
            logger.info(
                f"Cancelling {len(self.active_local_connections)} local command connections..."
            )
            for task in self.active_local_connections:
                task.cancel()

            await asyncio.gather(*self.active_local_connections, return_exceptions=True)
            logger.info("All local command connections closed")

        # Close the server (stop accepting new connections)
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Proxy server stopped accepting new connections")

        if self.local_command_server:
            self.local_command_server.close()
            await self.local_command_server.wait_closed()
            logger.info("Local command server stopped accepting new connections")

        logger.info("Proxy server stopped")

    async def serve_forever(self):
        """Serve the proxy server forever or until cancelled."""
        if not self.server or not self.local_command_server:
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
