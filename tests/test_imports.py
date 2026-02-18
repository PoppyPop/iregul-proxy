"""Test that all modules can be imported."""


def test_import_config() -> None:
    """Test that config module can be imported."""
    from iregul_proxy import config

    assert config is not None


def test_import_proxy() -> None:
    """Test that proxy module can be imported."""
    from iregul_proxy import proxy

    assert proxy is not None


def test_import_api() -> None:
    """Test that api module can be imported."""
    from iregul_proxy import api

    assert api is not None


def test_import_main() -> None:
    """Test that main module can be imported."""
    from iregul_proxy import main

    assert main is not None


def test_package_version() -> None:
    """Test that package has a version."""
    from iregul_proxy import __version__

    assert __version__ == "0.1.0"
