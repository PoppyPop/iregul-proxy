"""JSON API server for exposing heat pump data."""
import logging
from aiohttp import web
from typing import Optional

logger = logging.getLogger(__name__)


class APIServer:
    """HTTP API server for exposing heat pump data."""
    
    def __init__(self, host: str, port: int, proxy_server):
        """Initialize the API server.
        
        Args:
            host: Host to bind the API server to
            port: Port to bind the API server to
            proxy_server: ProxyServer instance to get data from
        """
        self.host = host
        self.port = port
        self.proxy_server = proxy_server
        self.app = web.Application()
        self._setup_routes()
        
    def _setup_routes(self):
        """Set up API routes."""
        self.app.router.add_get('/api/data', self.get_data)
        self.app.router.add_get('/api/health', self.health_check)
        self.app.router.add_get('/', self.index)
        
    async def get_data(self, request: web.Request) -> web.Response:
        """Get the last received heat pump data.
        
        Args:
            request: HTTP request
            
        Returns:
            JSON response with last data or error
        """
        data = self.proxy_server.get_last_data()
        
        if data is None:
            return web.json_response({
                'status': 'no_data',
                'message': 'No data received yet from heat pump'
            }, status=404)
            
        return web.json_response({
            'status': 'ok',
            'data': data
        })
        
    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint.
        
        Args:
            request: HTTP request
            
        Returns:
            JSON response with health status
        """
        return web.json_response({
            'status': 'healthy',
            'proxy_running': self.proxy_server.server is not None
        })
        
    async def index(self, request: web.Request) -> web.Response:
        """Index page with API documentation.
        
        Args:
            request: HTTP request
            
        Returns:
            HTML response with API documentation
        """
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>iRegul Proxy API</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
                h1 { color: #333; }
                .endpoint { background: #f4f4f4; padding: 15px; margin: 10px 0; border-radius: 5px; }
                code { background: #e0e0e0; padding: 2px 6px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <h1>iRegul Proxy API</h1>
            <p>Welcome to the iRegul Proxy Server API. This server acts as a proxy between your heat pump and the upstream server, 
            while exposing the last received data via a JSON API.</p>
            
            <h2>Available Endpoints</h2>
            
            <div class="endpoint">
                <h3>GET /api/data</h3>
                <p>Returns the last decoded data received from the heat pump.</p>
                <p>Example: <code>curl http://localhost:8080/api/data</code></p>
            </div>
            
            <div class="endpoint">
                <h3>GET /api/health</h3>
                <p>Health check endpoint to verify the server is running.</p>
                <p>Example: <code>curl http://localhost:8080/api/health</code></p>
            </div>
            
            <div class="endpoint">
                <h3>GET /</h3>
                <p>This documentation page.</p>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')
        
    async def start(self):
        """Start the API server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"API server started on http://{self.host}:{self.port}")
        return runner
