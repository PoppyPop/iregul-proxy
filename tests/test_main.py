"""Tests for main entrypoint orchestration."""

from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from typing import Any

import iregul_proxy.main as main_module


class FakeProxyServer:
    """Proxy server test double used by main() tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self.started = True

    async def serve_forever(self) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        self.stopped = True
        self._stop_event.set()


class FakeAPIRunner:
    """API runner test double with uvicorn-like serve loop."""

    def __init__(self) -> None:
        self.should_exit = False
        self.served = False

    async def serve(self) -> None:
        self.served = True
        while not self.should_exit:
            await asyncio.sleep(0)


class FakeAPIServer:
    """API server test double used by main() tests."""

    def __init__(self, host: str, port: int, proxy_server: FakeProxyServer) -> None:
        self.host = host
        self.port = port
        self.proxy_server = proxy_server
        self.runner = FakeAPIRunner()

    async def start(self) -> FakeAPIRunner:
        return self.runner


class FakeLoop:
    """Minimal loop object exposing add_signal_handler for main()."""

    def __init__(self) -> None:
        self.handlers: dict[signal.Signals, Any] = {}

    def add_signal_handler(self, sig: signal.Signals, callback: Any) -> None:
        self.handlers[sig] = callback


async def run_main_once(
    monkeypatch: Any, *, upstream_enabled: bool
) -> tuple[FakeProxyServer, FakeAPIServer]:
    """Run main() once with fully mocked dependencies."""
    config = SimpleNamespace(
        proxy_host="127.0.0.1",
        proxy_port=65001,
        upstream_host="127.0.0.1",
        upstream_port=65002,
        upstream_enabled=upstream_enabled,
        local_command_host="127.0.0.1",
        local_command_port=65011,
        api_host="127.0.0.1",
        api_port=8000,
        log_downstream=True,
        log_dir="/tmp",
        log_max_bytes=1024,
        log_backup_count=2,
        readuntil_timeout=1,
    )

    created: dict[str, Any] = {}

    def fake_proxy_ctor(*args: Any, **kwargs: Any) -> FakeProxyServer:
        proxy = FakeProxyServer(*args, **kwargs)
        created["proxy"] = proxy
        return proxy

    def fake_api_ctor(host: str, port: int, proxy_server: FakeProxyServer) -> FakeAPIServer:
        api = FakeAPIServer(host, port, proxy_server)
        created["api"] = api
        return api

    fake_loop = FakeLoop()

    monkeypatch.setattr(main_module.Config, "from_env", staticmethod(lambda: config))
    monkeypatch.setattr(main_module, "ProxyServer", fake_proxy_ctor)
    monkeypatch.setattr(main_module, "APIServer", fake_api_ctor)
    monkeypatch.setattr(main_module.asyncio, "get_running_loop", lambda: fake_loop)

    task = asyncio.create_task(main_module.main())
    await asyncio.sleep(0)

    # Trigger graceful shutdown through registered signal handler.
    fake_loop.handlers[signal.SIGINT]()

    await asyncio.wait_for(task, timeout=2.0)

    return created["proxy"], created["api"]


async def test_main_graceful_shutdown_with_upstream_enabled(monkeypatch: Any) -> None:
    """main() starts and stops both servers when a signal is received."""
    proxy, api = await run_main_once(monkeypatch, upstream_enabled=True)

    assert proxy.started is True
    assert proxy.stopped is True
    assert api.runner.served is True
    assert api.runner.should_exit is True


async def test_main_graceful_shutdown_with_upstream_disabled(monkeypatch: Any) -> None:
    """main() also runs correctly when upstream forwarding is disabled."""
    proxy, api = await run_main_once(monkeypatch, upstream_enabled=False)

    assert proxy.kwargs["upstream_enabled"] is False
    assert proxy.started is True
    assert proxy.stopped is True
    assert api.runner.should_exit is True


def test_run_calls_asyncio_run(monkeypatch: Any) -> None:
    """run() delegates to asyncio.run(main())."""
    called = {"count": 0}

    def fake_asyncio_run(coroutine: Any) -> None:
        coroutine.close()
        called["count"] += 1

    monkeypatch.setattr(main_module.asyncio, "run", fake_asyncio_run)

    main_module.run()

    assert called["count"] == 1


def test_run_exits_cleanly_on_keyboard_interrupt(monkeypatch: Any) -> None:
    """run() handles KeyboardInterrupt and exits with code 0."""
    exits: list[int] = []

    def fake_asyncio_run(coroutine: Any) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    def fake_exit(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(main_module.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(main_module.sys, "exit", fake_exit)

    try:
        main_module.run()
    except SystemExit:
        pass

    assert exits == [0]
