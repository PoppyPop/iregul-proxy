"""Configuration management for iRegul Proxy."""

import dataclasses
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
    upstream_host: str = "82.165.167.253"  # "i-regul.fr"
    upstream_port: int = 65001

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Logging settings
    log_downstream: bool = True
    log_dir: str = "logs"
    log_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_backup_count: int = 8

    # Message reading timeout (seconds)
    readuntil_timeout: int = 75

    @classmethod
    def from_env(cls) -> Config:
        """Create configuration from environment variables."""
        d = {f.name: f.default for f in dataclasses.fields(cls)}
        return cls(
            proxy_host=os.getenv("PROXY_HOST", str(d["proxy_host"])),
            proxy_port=int(os.getenv("PROXY_PORT", str(d["proxy_port"]))),
            upstream_host=os.getenv("UPSTREAM_HOST", str(d["upstream_host"])),
            upstream_port=int(os.getenv("UPSTREAM_PORT", str(d["upstream_port"]))),
            api_host=os.getenv("API_HOST", str(d["api_host"])),
            api_port=int(os.getenv("API_PORT", str(d["api_port"]))),
            log_downstream=os.getenv("LOG_DOWNSTREAM", str(d["log_downstream"]).lower()).lower()
            in ("true", "1", "yes"),
            log_dir=os.getenv("LOG_DIR", str(d["log_dir"])),
            log_max_bytes=int(os.getenv("LOG_MAX_BYTES", str(d["log_max_bytes"]))),
            log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", str(d["log_backup_count"]))),
            readuntil_timeout=int(os.getenv("READUNTIL_TIMEOUT", str(d["readuntil_timeout"]))),
        )
