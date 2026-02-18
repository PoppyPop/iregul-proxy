"""Tests for configuration module."""

import os
from unittest.mock import patch

from iregul_proxy.config import Config


def test_config_defaults() -> None:
    """Test default configuration values."""
    config = Config()
    assert config.proxy_host == "0.0.0.0"
    assert config.proxy_port == 65001
    assert config.api_host == "0.0.0.0"


def test_config_from_env() -> None:
    """Test configuration from environment variables."""
    with patch.dict(
        os.environ,
        {
            "PROXY_HOST": "127.0.0.1",
            "PROXY_PORT": "65002",
            "UPSTREAM_HOST": "test.example.com",
            "UPSTREAM_PORT": "65003",
            "API_HOST": "127.0.0.1",
            "API_PORT": "8081",
        },
    ):
        config = Config.from_env()
        assert config.proxy_host == "127.0.0.1"
        assert config.proxy_port == 65002
        assert config.upstream_host == "test.example.com"
        assert config.upstream_port == 65003
        assert config.api_host == "127.0.0.1"
        assert config.api_port == 8081


def test_config_partial_env() -> None:
    """Test configuration with partial environment variables."""
    with patch.dict(
        os.environ,
        {
            "PROXY_PORT": "65010",
        },
        clear=True,
    ):
        config = Config.from_env()
        assert config.proxy_port == 65010
        assert config.proxy_host == "0.0.0.0"  # Default value
