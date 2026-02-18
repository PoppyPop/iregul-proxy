"""Proxy server that receives heat pump data and forwards it upstream."""
import asyncio
import logging
from typing import Optional
from aioiregul.v2 import decoder

logger = logging.getLogger(__name__)


class ProxyServer:
    """Proxy server for iRegul heat pump communication."""
    
    def __init__(self, proxy_host: str, proxy_port: int, 
                 upstream_host: str, upstream_port: int):
        """Initialize the proxy server.
        
        Args:
            proxy_host: Host to bind the proxy server to
            proxy_port: Port to bind the proxy server to
            upstream_host: Upstream server host to forward messages to
            upstream_port: Upstream server port to forward messages to
        """
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.server: Optional[asyncio.Server] = None
        self.last_data: Optional[dict] = None
        self.last_raw_message: Optional[str] = None
        
    async def handle_client(self, reader: asyncio.StreamReader, 
                           writer: asyncio.StreamWriter):
        """Handle a client connection.
        
        Args:
            reader: Stream reader for receiving data from client
            writer: Stream writer for sending data to client
        """
        addr = writer.get_extra_info('peername')
        logger.info(f"Connection from {addr}")
        
        try:
            # Connect to upstream server
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host, self.upstream_port
            )
            logger.info(f"Connected to upstream server {self.upstream_host}:{self.upstream_port}")
            
            # Create tasks for bidirectional forwarding
            client_to_upstream = asyncio.create_task(
                self._forward_data(reader, upstream_writer, "client->upstream")
            )
            upstream_to_client = asyncio.create_task(
                self._forward_data(upstream_reader, writer, "upstream->client")
            )
            
            # Wait for both tasks to complete
            await asyncio.gather(client_to_upstream, upstream_to_client)
            
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            logger.info(f"Closing connection from {addr}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
                
    async def _forward_data(self, reader: asyncio.StreamReader, 
                          writer: asyncio.StreamWriter, direction: str):
        """Forward data between reader and writer.
        
        Args:
            reader: Stream reader to read data from
            writer: Stream writer to write data to
            direction: Direction description for logging
        """
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                    
                # Log and decode if data is coming from client (heat pump)
                if direction == "client->upstream":
                    try:
                        text_data = data.decode('utf-8', errors='ignore')
                        self.last_raw_message = text_data
                        logger.debug(f"Received from client: {text_data[:100]}")
                        
                        # Try to decode the message
                        try:
                            decoded = decoder.decode_text(text_data)
                            self.last_data = {
                                'timestamp': decoded.timestamp.isoformat() if decoded.timestamp else None,
                                'is_old': decoded.is_old,
                                'count': decoded.count,
                                'groups': decoded.groups,
                                'raw': text_data
                            }
                            logger.info(f"Successfully decoded frame: timestamp={decoded.timestamp}, groups={len(decoded.groups)}")
                        except Exception as e:
                            logger.warning(f"Failed to decode message: {e}")
                    except Exception as e:
                        logger.debug(f"Failed to decode as text: {e}")
                
                # Forward the data
                writer.write(data)
                await writer.drain()
                
        except asyncio.CancelledError:
            logger.debug(f"Forward task cancelled: {direction}")
            raise
        except Exception as e:
            logger.error(f"Error forwarding data ({direction}): {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
                
    async def start(self):
        """Start the proxy server."""
        self.server = await asyncio.start_server(
            self.handle_client, self.proxy_host, self.proxy_port
        )
        
        addr = self.server.sockets[0].getsockname()
        logger.info(f"Proxy server started on {addr[0]}:{addr[1]}")
        logger.info(f"Forwarding to {self.upstream_host}:{self.upstream_port}")
        
    async def stop(self):
        """Stop the proxy server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Proxy server stopped")
            
    async def serve_forever(self):
        """Serve the proxy server forever."""
        if self.server:
            async with self.server:
                await self.server.serve_forever()
                
    def get_last_data(self) -> Optional[dict]:
        """Get the last decoded data received from the heat pump.
        
        Returns:
            Dictionary with last decoded data or None if no data received yet
        """
        return self.last_data
