"""Main entry point for iRegul proxy server."""

import asyncio
import logging
import signal
import sys

from .api import APIServer
from .config import Config
from .proxy import ProxyServer

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Main function to run the proxy and API servers."""
    # Load configuration
    config = Config.from_env()

    logger.info("Starting iRegul Proxy Server")
    logger.info(
        f"Proxy: {config.proxy_host}:{config.proxy_port} -> {config.upstream_host}:{config.upstream_port}"
    )
    logger.info(f"API: http://{config.api_host}:{config.api_port}")

    # Create servers
    proxy_server = ProxyServer(
        config.proxy_host,
        config.proxy_port,
        config.upstream_host,
        config.upstream_port,
        log_downstream=config.log_downstream,
        log_dir=config.log_dir,
        log_max_bytes=config.log_max_bytes,
        log_backup_count=config.log_backup_count,
        readuntil_timeout=config.readuntil_timeout,
    )

    api_server = APIServer(config.api_host, config.api_port, proxy_server)

    # Start servers
    await proxy_server.start()
    api_runner = await api_server.start()

    # Set up graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Create tasks for proxy and API servers
        proxy_task = asyncio.create_task(proxy_server.serve_forever())
        api_task = asyncio.create_task(api_runner.serve())

        # Wait for shutdown signal
        await shutdown_event.wait()

        logger.info("Initiating graceful shutdown...")

        # Stop proxy server (this will close connections and set shutdown event)
        await proxy_server.stop()

        # Signal API server to exit
        api_runner.should_exit = True

        # Wait for both tasks to complete gracefully
        await asyncio.gather(proxy_task, api_task, return_exceptions=True)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Error during server operation: {e}")
    finally:
        # Final cleanup (in case stop wasn't called)
        logger.info("Final cleanup...")
        if proxy_server.server:
            await proxy_server.stop()
        logger.info("Shutdown complete")


def run():
    """Entry point function."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    run()
