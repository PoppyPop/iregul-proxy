"""Tests for proxy server graceful shutdown."""

import asyncio
import contextlib
import logging
from typing import Any

import pytest

from iregul_proxy.proxy import ProxyServer

logger = logging.getLogger(__name__)


class MockUpstreamServer:
    """Mock upstream server for testing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        """Initialize the mock upstream server.

        Args:
            host: Host to bind to
            port: Port to bind to (0 for automatic assignment)
        """
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self.client_handlers: set[asyncio.Task[None]] = set()
        self.received_messages: list[bytes] = []
        self.should_respond = True
        self.response_message = b'{"status":"ok"}'
        self._started = asyncio.Event()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client connection.

        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        addr = writer.get_extra_info("peername")
        logger.info(f"Mock upstream: Connection from {addr}")

        try:
            while True:
                # Read data until we get a message ending with }
                try:
                    data = await reader.readuntil(b"}")
                except asyncio.IncompleteReadError as e:
                    data = e.partial
                    if not data:
                        break

                if not data:
                    break

                logger.info(f"Mock upstream: Received {len(data)} bytes from {addr}")
                self.received_messages.append(data)

                # Send a response if configured to do so
                if self.should_respond:
                    writer.write(self.response_message)
                    await writer.drain()
                    logger.info(f"Mock upstream: Sent response to {addr}")

        except asyncio.CancelledError:
            logger.info(f"Mock upstream: Handler cancelled for {addr}")
            raise
        except Exception as e:
            logger.error(f"Mock upstream: Error handling client {addr}: {e}")
        finally:
            logger.info(f"Mock upstream: Closing connection from {addr}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _handle_client_wrapper(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrapper to track client connections.

        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        task = asyncio.create_task(self.handle_client(reader, writer))
        self.client_handlers.add(task)
        task.add_done_callback(self.client_handlers.discard)

    async def start(self) -> None:
        """Start the mock upstream server."""
        self.server = await asyncio.start_server(self._handle_client_wrapper, self.host, self.port)
        # Get the actual port if we used 0
        if self.port == 0:
            self.port = self.server.sockets[0].getsockname()[1]
        logger.info(f"Mock upstream server started on {self.host}:{self.port}")
        self._started.set()

    async def stop(self) -> None:
        """Stop the mock upstream server."""
        logger.info("Stopping mock upstream server...")

        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Mock upstream server stopped accepting new connections")

        # Cancel all active client handlers
        if self.client_handlers:
            logger.info(f"Cancelling {len(self.client_handlers)} active handlers...")
            for task in self.client_handlers:
                task.cancel()
            await asyncio.gather(*self.client_handlers, return_exceptions=True)
            logger.info("All mock upstream handlers closed")

        logger.info("Mock upstream server stopped")

    async def wait_started(self) -> None:
        """Wait for the server to start."""
        await self._started.wait()


@pytest.fixture
async def mock_upstream() -> Any:
    """Fixture for mock upstream server.

    Yields:
        MockUpstreamServer instance
    """
    server = MockUpstreamServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def proxy_server(mock_upstream: MockUpstreamServer) -> Any:
    """Fixture for proxy server connected to mock upstream.

    Args:
        mock_upstream: Mock upstream server fixture

    Yields:
        ProxyServer instance
    """
    await mock_upstream.wait_started()

    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,  # Use any available port
        upstream_host=mock_upstream.host,
        upstream_port=mock_upstream.port,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=10485760,
        log_backup_count=5,
    )
    await proxy.start()

    # Get the actual port assigned
    if proxy.server and proxy.server.sockets:
        proxy.proxy_port = proxy.server.sockets[0].getsockname()[1]

    try:
        yield proxy
    finally:
        await proxy.stop()


async def test_shutdown_without_active_connections(
    proxy_server: ProxyServer, mock_upstream: MockUpstreamServer
) -> None:
    """Test graceful shutdown without any active connections.

    This test verifies that the proxy server can shut down cleanly
    when there are no active client connections.

    Args:
        proxy_server: Proxy server fixture
        mock_upstream: Mock upstream server fixture
    """
    # Give the server a moment to stabilize
    await asyncio.sleep(0.1)

    # Verify server is running
    assert proxy_server.server is not None
    assert len(proxy_server.active_connections) == 0

    # Start the serve_forever task
    serve_task = asyncio.create_task(proxy_server.serve_forever())

    # Give it a moment to start serving
    await asyncio.sleep(0.1)

    # Now stop the server
    stop_task = asyncio.create_task(proxy_server.stop())

    # Wait for both tasks to complete with a timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(serve_task, stop_task, return_exceptions=True), timeout=5.0
        )
    except TimeoutError:
        pytest.fail("Shutdown timed out - server did not shut down gracefully")

    # Verify shutdown completed
    assert proxy_server._shutdown_event.is_set()
    assert len(proxy_server.active_connections) == 0


async def test_shutdown_with_active_connection(
    proxy_server: ProxyServer, mock_upstream: MockUpstreamServer
) -> None:
    """Test graceful shutdown with an active client connection.

    This test verifies that the proxy server can shut down cleanly
    even when there is an active client connection forwarding data.

    Args:
        proxy_server: Proxy server fixture
        mock_upstream: Mock upstream server fixture
    """
    # Start the serve_forever task
    serve_task = asyncio.create_task(proxy_server.serve_forever())

    # Give it a moment to start serving
    await asyncio.sleep(0.1)

    # Create a client connection
    client_reader, client_writer = await asyncio.open_connection(
        proxy_server.proxy_host, proxy_server.proxy_port
    )

    try:
        # Give the connection a moment to establish
        await asyncio.sleep(0.2)

        # Verify we have an active connection
        assert len(proxy_server.active_connections) == 1

        # Send some data through the proxy
        test_message = b'{"test": "message"}'
        client_writer.write(test_message)
        await client_writer.drain()

        # Give it time to forward
        await asyncio.sleep(0.2)

        # Verify the message was received by mock upstream
        assert len(mock_upstream.received_messages) > 0
        assert test_message in mock_upstream.received_messages

        # Now initiate shutdown while connection is active
        stop_task = asyncio.create_task(proxy_server.stop())

        # Wait for shutdown to complete with a timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(serve_task, stop_task, return_exceptions=True), timeout=5.0
            )
        except TimeoutError:
            pytest.fail(
                "Shutdown timed out with active connection - server did not shut down gracefully"
            )

        # Verify shutdown completed
        assert proxy_server._shutdown_event.is_set()
        assert len(proxy_server.active_connections) == 0

        # Try to read from client - should get EOF or connection reset
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            await asyncio.wait_for(client_reader.read(1024), timeout=1.0)

    finally:
        # Clean up client connection
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def test_shutdown_with_multiple_active_connections(
    proxy_server: ProxyServer, mock_upstream: MockUpstreamServer
) -> None:
    """Test graceful shutdown with multiple active client connections.

    This test verifies that the proxy server can shut down cleanly
    when there are multiple active client connections.

    Args:
        proxy_server: Proxy server fixture
        mock_upstream: Mock upstream server fixture
    """
    # Start the serve_forever task
    serve_task = asyncio.create_task(proxy_server.serve_forever())

    # Give it a moment to start serving
    await asyncio.sleep(0.1)

    # Create multiple client connections
    num_clients = 3
    clients: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

    try:
        for _ in range(num_clients):
            reader, writer = await asyncio.open_connection(
                proxy_server.proxy_host, proxy_server.proxy_port
            )
            clients.append((reader, writer))
            await asyncio.sleep(0.1)  # Stagger connection creation

        # Verify we have all connections active
        await asyncio.sleep(0.2)
        assert len(proxy_server.active_connections) == num_clients

        # Send data through each client
        for i, (_, writer) in enumerate(clients):
            test_message = f'{{"test": "message_{i}"}}'.encode()
            writer.write(test_message)
            await writer.drain()

        # Give time for messages to forward
        await asyncio.sleep(0.3)

        # Verify messages were received
        assert len(mock_upstream.received_messages) >= num_clients

        # Now initiate shutdown while all connections are active
        stop_task = asyncio.create_task(proxy_server.stop())

        # Wait for shutdown to complete with a timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(serve_task, stop_task, return_exceptions=True), timeout=5.0
            )
        except TimeoutError:
            pytest.fail(
                f"Shutdown timed out with {num_clients} active connections - "
                "server did not shut down gracefully"
            )

        # Verify shutdown completed
        assert proxy_server._shutdown_event.is_set()
        assert len(proxy_server.active_connections) == 0

    finally:
        # Clean up all client connections
        for _, writer in clients:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
