"""Configuration management for iRegul Proxy."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Configuration for the iRegul proxy server."""
    
    # Proxy server settings
    proxy_host: str = "0.0.0.0"
    proxy_port: int = 65001
    
    # Upstream server settings
    upstream_host: str = os.getenv("UPSTREAM_HOST", "cloud.iregul.com")
    upstream_port: int = int(os.getenv("UPSTREAM_PORT", "65001"))
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = int(os.getenv("API_PORT", "8080"))
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls(
            proxy_host=os.getenv("PROXY_HOST", "0.0.0.0"),
            proxy_port=int(os.getenv("PROXY_PORT", "65001")),
            upstream_host=os.getenv("UPSTREAM_HOST", "cloud.iregul.com"),
            upstream_port=int(os.getenv("UPSTREAM_PORT", "65001")),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8080")),
        )
