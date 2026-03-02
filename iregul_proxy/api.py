"""JSON API server for exposing heat pump data."""

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from .proxy import ProxyServer

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    proxy_running: bool


class DataResponse(BaseModel):
    """Data response."""

    status: str
    data: dict[str, Any] | None = None
    message: str | None = None


class APIServer:
    """HTTP API server for exposing heat pump data."""

    def __init__(self, host: str, port: int, proxy_server: ProxyServer):
        """Initialize the API server.

        Args:
            host: Host to bind the API server to
            port: Port to bind the API server to
            proxy_server: ProxyServer instance to get data from
        """
        self.host = host
        self.port = port
        self.proxy_server = proxy_server
        self.app = FastAPI(
            title="iRegul Proxy API",
            description="API for exposing heat pump data from iRegul proxy server",
            version="0.1.0",
        )
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up API routes."""

        @self.app.get(
            "/api/data",
            response_model=DataResponse,
            summary="Get latest heat pump data",
            description="Returns the last decoded data received from the heat pump.",
        )
        async def get_data() -> DataResponse:  # noqa: ARG001
            """Get the last received heat pump data.

            Returns:
                DataResponse with last data or error message
            """
            data = self.proxy_server.get_last_data()

            if data is None:
                return DataResponse(
                    status="no_data",
                    message="No data received yet from heat pump",
                )

            return DataResponse(status="ok", data=data)

        @self.app.get(
            "/api/health",
            response_model=HealthResponse,
            summary="Health check",
            description="Health check endpoint to verify the server is running.",
        )
        async def health_check() -> HealthResponse:  # noqa: ARG001
            """Health check endpoint.

            Returns:
                HealthResponse with health status
            """
            return HealthResponse(
                status="healthy",
                proxy_running=self.proxy_server.server is not None,
            )

    async def start(self):
        """Start the API server.

        Returns:
            AsyncServer runner instance
        """
        from uvicorn import Config, Server

        config = Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = Server(config)
        logger.info(f"API server started on http://{self.host}:{self.port}")
        logger.info(f"Swagger documentation available at http://{self.host}:{self.port}/docs")
        return server
