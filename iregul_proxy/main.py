"""Main entry point for iRegul proxy server."""
import asyncio
import logging
import signal
import sys
from .config import Config
from .proxy import ProxyServer
from .api import APIServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Main function to run the proxy and API servers."""
    # Load configuration
    config = Config.from_env()
    
    logger.info("Starting iRegul Proxy Server")
    logger.info(f"Proxy: {config.proxy_host}:{config.proxy_port} -> {config.upstream_host}:{config.upstream_port}")
    logger.info(f"API: http://{config.api_host}:{config.api_port}")
    
    # Create servers
    proxy_server = ProxyServer(
        config.proxy_host,
        config.proxy_port,
        config.upstream_host,
        config.upstream_port
    )
    
    api_server = APIServer(
        config.api_host,
        config.api_port,
        proxy_server
    )
    
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
        # Create task for proxy server
        proxy_task = asyncio.create_task(proxy_server.serve_forever())
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        # Cancel proxy task
        proxy_task.cancel()
        try:
            await proxy_task
        except asyncio.CancelledError:
            pass
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        # Cleanup
        logger.info("Shutting down servers...")
        await proxy_server.stop()
        await api_runner.cleanup()
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
