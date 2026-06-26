"""Full integration tests for upstream and local command routing."""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import AsyncGenerator
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest
from aioiregul.v2 import decoder

from iregul_proxy.proxy import ProxyServer

logger = logging.getLogger(__name__)


class MockUpstreamEndpoint:
    """Minimal upstream server endpoint controlled by tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self._connected = asyncio.Event()
        self._disconnect = asyncio.Event()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._connected.set()

        try:
            await self._disconnect.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        if self.server.sockets:
            self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._disconnect.set()
        if self._writer is not None and not self._writer.is_closing():
            self._writer.close()
            await self._writer.wait_closed()

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def wait_connected(self) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=2.0)

    async def send_request(self, payload: bytes) -> None:
        if self._writer is None:
            raise RuntimeError("Upstream writer is not connected")
        self._writer.write(payload)
        await self._writer.drain()

    async def read_response(self) -> bytes:
        if self._reader is None:
            raise RuntimeError("Upstream reader is not connected")
        return await asyncio.wait_for(self._reader.readuntil(b"}"), timeout=2.0)


@pytest.fixture
async def proxy_with_upstream() -> AsyncGenerator[tuple[ProxyServer, MockUpstreamEndpoint]]:
    """Start proxy server wired to a controllable upstream endpoint."""
    upstream = MockUpstreamEndpoint()
    await upstream.start()

    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host=upstream.host,
        upstream_port=upstream.port,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    try:
        yield proxy, upstream
    finally:
        await proxy.stop()
        await upstream.stop()


@pytest.fixture
async def proxy_without_upstream() -> AsyncGenerator[ProxyServer]:
    """Start proxy server with upstream forwarding disabled."""
    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host="127.0.0.1",
        upstream_port=65002,
        upstream_enabled=False,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    try:
        yield proxy
    finally:
        await proxy.stop()


async def test_full_routing_between_upstream_local_and_downstream(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route upstream and local requests to downstream and back to proper requester."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-upstream-response{u-ok#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="200",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-ok#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    try:
        # Upstream request path.
        await upstream.send_request(b"{200#}")
        upstream_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert upstream_to_downstream == b"{200#}"

        hp_writer.write(b"hp-upstream-response{u-ok#}")
        await hp_writer.drain()

        upstream_response = await upstream.read_response()
        assert upstream_response == b"hp-upstream-response{u-ok#}"

        # Local command path.
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-ok#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-ok#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_concurrent_upstream_and_local_commands_are_serialized_and_routed(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent upstream/local requests are serialized and answered correctly."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> Any:
        if text_data == "hp-upstream-response{u-conc#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="200",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-conc#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    async def send_local_and_read() -> str:
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()
            raw = await asyncio.wait_for(local_reader.read(1024), timeout=2.0)
            return raw.decode("utf-8", errors="ignore")
        finally:
            local_writer.close()
            await local_writer.wait_closed()

    try:
        # Trigger upstream first so it takes the request lock.
        await upstream.send_request(b"{200#}")
        first_downstream_request = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert first_downstream_request == b"{200#}"

        local_started_at = monotonic()
        local_task = asyncio.create_task(send_local_and_read())

        await asyncio.sleep(0.1)
        assert not local_task.done()

        await asyncio.sleep(0.2)
        hp_writer.write(b"hp-upstream-response{u-conc#}")
        await hp_writer.drain()

        upstream_response = await upstream.read_response()
        assert upstream_response == b"hp-upstream-response{u-conc#}"

        second_downstream_request = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert second_downstream_request == b"cdraminfoDEV1PWD1{10#}"

        hp_writer.write(b"hp-local-response{l-conc#}")
        await hp_writer.drain()

        local_response = await local_task
        assert re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-conc#\}$", local_response)
        assert monotonic() - local_started_at >= 0.2
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_upstream_dt_config_message_is_forwarded_to_downstream(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream DT_config requests are forwarded downstream and answered upstream."""
    proxy, upstream = proxy_with_upstream

    upstream_payload = b"{12#DT_config@0&autorisation_chauffage[0]#DT_config@0&autorisation_rafraichissement[1]}"
    downstream_payload = (
        b"cdraminfo106949{12#mem@0&etat[10]#mem@0&sous_etat[20]#mem@0&alarme[0]#"
        b"C@0&autorisation_chauffage[0]#C@0&autorisation_rafraichissement[1]#"
        b"C@0&autorisation_rampe[0]#Z@1&consigne_normal[50]#Z@1&consigne_reduit[40]#"
        b"Z@1&consigne_horsgel[10]#Z@1&mode_select[0]#Z@1&mode[4]#"
        b"Z@2&consigne_normal[25]#Z@2&consigne_reduit[20]#Z@2&consigne_horsgel[10]#"
        b"Z@2&mode_select[0]#Z@2&mode[4]#Z@3&consigne_normal[35]#Z@3&consigne_reduit[35]#"
        b"Z@3&consigne_horsgel[10]#Z@3&mode_select[0]#Z@3&mode[4]#"
        b"Z@4&consigne_normal[10]#Z@4&consigne_reduit[10]#Z@4&consigne_horsgel[10]#"
        b"Z@4&mode_select[0]#Z@4&mode[4]#Z@5&consigne_normal[20]#Z@5&consigne_reduit[19]#"
        b"Z@5&consigne_horsgel[10]#Z@5&mode_select[0]#Z@5&mode[4]#"
        b"Z@6&consigne_normal[80]#Z@6&consigne_reduit[40]#Z@6&consigne_horsgel[10]#"
        b"Z@6&mode_select[0]#Z@6&mode[4]#Z@7&consigne_normal[80]#Z@7&consigne_reduit[40]#"
        b"Z@7&consigne_horsgel[10]#Z@7&mode_select[0]#Z@7&mode[4]#"
        b"Z@8&consigne_normal[25]#Z@8&consigne_reduit[20]#Z@8&consigne_horsgel[10]#"
        b"Z@8&mode_select[0]#Z@8&mode[4]#Z@9&consigne_normal[20]#Z@9&consigne_reduit[19]#"
        b"Z@9&consigne_horsgel[10]#Z@9&mode_select[0]#Z@9&mode[4]#"
        b"Z@10&consigne_normal[20]#Z@10&consigne_reduit[19]#Z@10&consigne_horsgel[10]#"
        b"Z@10&mode_select[0]#Z@10&mode[4]#Z@11&consigne_normal[20.5]#"
        b"Z@11&consigne_reduit[16]#Z@11&consigne_horsgel[10]#Z@11&mode_select[1]#"
        b"Z@11&mode[0]#Z@12&consigne_normal[20.5]#Z@12&consigne_reduit[16]#"
        b"Z@12&consigne_horsgel[10]#Z@12&mode_select[1]#Z@12&mode[0]#"
        b"mem@0&test_sorties[False]#mem@0&test_sondes[False]#mem@0&test_entrees[False]#"
        b"mem@0&test_mesures[False]#mem@0&alarme_flag[False]#mem@0&solar_fct[0]#"
        b"mem@0&flag_ecs_elec_1[False]#mem@0&flag_ecs_elec_2[False]#"
        b"mem@0&alarme_num_sonde[0]#mem@0&journal[initialisation]#I@1&valeur[0]#"
        b"I@9&valeur[1]#I@13&valeur[0]#I@14&valeur[0]#I@30&valeur[0]#I@31&valeur[0]#"
        b"I@32&valeur[0]#I@33&valeur[0]#I@34&valeur[0]#O@3&valeur[0]#O@4&valeur[1]#"
        b"O@6&valeur[0]#O@8&valeur[0]#O@10&valeur[0]#O@12&valeur[0]#O@13&valeur[0]#"
        b"O@25&valeur[0]#O@26&valeur[0]#O@97&valeur[0]#O@98&valeur[0]#O@99&valeur[0]#"
        b"O@100&valeur[1]#O@60&valeur[1]#O@101&valeur[0]#O@110&valeur[0]#"
        b"O@120&valeur[0]#O@121&valeur[0]#O@122&valeur[0]#O@50&valeur[1]#"
        b"O@451&valeur[0]#O@52&valeur[100]#O@53&valeur[100]#O@54&valeur[100]#"
        b"O@55&valeur[100]#O@61&valeur[0]#O@62&valeur[0]#O@63&valeur[0]#O@64&valeur[0]#"
        b"O@606&valeur[0]#O@625&valeur[0]#O@626&valeur[0]#A@3&valeur[32.5]#"
        b"A@4&valeur[21.2]#A@5&valeur[20.8]#A@6&valeur[13.7]#A@7&valeur[14]#"
        b"A@15&valeur[21.2]#A@16&valeur[21.7]#A@17&valeur[24.3]#A@21&valeur[18.6]#"
        b"A@28&valeur[28.4]#A@29&valeur[25.7]#A@100&valeur[20.8]#A@101&valeur[20.9]#"
        b"A@110&valeur[24.1]#A@120&valeur[24.1]#A@121&valeur[23.4]#M@1&valeur[3.1]#"
        b"M@2&valeur[7.2]#M@3&valeur[15.53333]#M@4&valeur[7.200269]#M@5&valeur[0]#"
        b"M@6&valeur[0]#M@7&valeur[0]#M@8&valeur[0]#M@11&valeur[0]#M@12&valeur[-2.7]#"
        b"M@15&valeur[0]#M@16&valeur[514.8]#M@17&valeur[9]#M@19&valeur[10820]#"
        b"M@20&valeur[0.377622]#M@21&valeur[12.84893]#M@22&valeur[0]#"
        b"M@25&valeur[0.1186667]#M@26&valeur[13.34524]#M@27&valeur[0.001]#"
        b"M@28&valeur[0]#M@29&valeur[0]#M@30&valeur[195.5187]#M@31&valeur[8872.496]#"
        b"M@32&valeur[299.2932]#M@35&valeur[72.05021]#M@36&valeur[9439.521]#"
        b"M@37&valeur[0.906]#M@38&valeur[0]#M@39&valeur[0]#M@40&valeur[0]#"
        b"M@41&valeur[0.05344445]#M@42&valeur[0]#M@45&valeur[0]#"
        b"M@46&valeur[0.05344445]#M@47&valeur[0]#M@48&valeur[0]#M@49&valeur[0]#"
        b"M@50&valeur[0]#M@51&valeur[4.7762]#M@52&valeur[0]#M@55&valeur[0.0501]#"
        b"M@56&valeur[4.8263]#M@57&valeur[0]#M@60&valeur[0]#M@61&valeur[4491.154]#"
        b"M@62&valeur[71.4426]#M@65&valeur[52.9724]#M@66&valeur[4616.997]#"
        b"M@67&valeur[0]#M@70&valeur[0]#M@71&valeur[0.0167]#M@72&valeur[0]#"
        b"M@75&valeur[0]#M@76&valeur[0.0167]#M@77&valeur[0]#M@100&valeur[0]#"
        b"M@99&valeur[110]#B@0&resultat[0]#B@1&resultat[1]#B@2&resultat[0]#"
        b"B@3&resultat[0]#B@4&resultat[0]#B@5&resultat[0]#B@6&resultat[0]#B@7&resultat[0]#"
        b"B@8&resultat[4]#B@9&resultat[4]#B@10&resultat[0]#B@11&resultat[0]#"
        b"B@12&resultat[0]#B@13&resultat[0]#B@14&resultat[592]#B@15&resultat[29]#"
        b"B@16&resultat[48786]#B@17&resultat[4443]#B@18&resultat[1413]#"
        b"B@19&resultat[5148]#B@20&resultat[9]#B@21&resultat[1]#B@22&resultat[0]#"
        b"B@23&resultat[0]#B@24&resultat[0]#B@25&resultat[1]#B@26&resultat[0]#"
        b"B@27&resultat[4]#B@28&resultat[4]#B@29&resultat[4]#B@30&resultat[4]#"
        b"B@31&resultat[0]#B@32&resultat[0]#B@33&resultat[0]#B@34&resultat[0]#"
        b"B@35&resultat[0]#B@36&resultat[0]#B@37&resultat[0]#B@38&resultat[0]#"
        b"B@39&resultat[0]#B@40&resultat[0]#B@41&resultat[0]#B@42&resultat[0]#"
        b"B@43&resultat[0]#B@44&resultat[0]#B@45&resultat[0]#B@46&resultat[0]#"
        b"B@47&resultat[0]#B@48&resultat[0]#B@49&resultat[0]#B@50&resultat[0]#"
        b"B@51&resultat[0]#B@52&resultat[0]#B@53&resultat[0]#B@54&resultat[0]#"
        b"B@55&resultat[0]#B@56&resultat[0]#B@57&resultat[0]#B@58&resultat[0]#"
        b"B@59&resultat[0]#B@60&resultat[0]#B@61&resultat[0]#B@62&resultat[0]#"
        b"B@63&resultat[0]#B@64&resultat[0]#B@65&resultat[0]#B@66&resultat[0]#"
        b"B@67&resultat[0]#B@68&resultat[0]#B@69&resultat[0]#B@70&resultat[0]#"
        b"B@71&resultat[0]#B@72&resultat[0]#B@73&resultat[0]#B@74&resultat[0]#"
        b"B@75&resultat[0]#B@76&resultat[0]#B@77&resultat[0]#B@78&resultat[0]#"
        b"B@79&resultat[0]#B@80&resultat[0]#B@0&etat[init]#B@1&etat[ok E]#B@2&etat[ok E]#"
        b"B@3&etat[init]#B@4&etat[init]#B@5&etat[ok E]#B@6&etat[ok L]#B@7&etat[ok L]#"
        b"B@8&etat[send]#B@9&etat[ok L]#B@10&etat[ok L]#B@11&etat[ok L]#B@12&etat[ok L]#"
        b"B@13&etat[ok L]#B@14&etat[ok L]#B@15&etat[ok L]#B@16&etat[ok L]#"
        b"B@17&etat[ok L]#B@18&etat[ok L]#B@19&etat[ok L]#B@20&etat[ok L]#"
        b"B@21&etat[ok L]#B@22&etat[ok L]#B@23&etat[ok L]#B@24&etat[ok L]#"
        b"B@25&etat[ok L]#B@26&etat[ok L]#B@27&etat[ok L]#B@28&etat[ok L]#"
        b"B@29&etat[ok L]#B@30&etat[ok L]#B@31&etat[ok L]#B@32&etat[ok L]#"
        b"B@33&etat[init]#B@34&etat[init]#B@35&etat[init]#B@36&etat[init]#"
        b"B@37&etat[init]#B@38&etat[init]#B@39&etat[init]#B@40&etat[init]#"
        b"B@41&etat[init]#B@42&etat[init]#B@43&etat[init]#B@44&etat[init]#"
        b"B@45&etat[init]#B@46&etat[init]#B@47&etat[init]#B@48&etat[init]#"
        b"B@49&etat[init]#B@50&etat[init]#B@51&etat[init]#B@52&etat[init]#"
        b"B@53&etat[init]#B@54&etat[init]#B@55&etat[init]#B@56&etat[init]#"
        b"B@57&etat[init]#B@58&etat[init]#B@59&etat[init]#B@60&etat[init]#"
        b"B@61&etat[init]#B@62&etat[init]#B@63&etat[init]#B@64&etat[init]#"
        b"B@65&etat[init]#B@66&etat[init]#B@67&etat[init]#B@68&etat[init]#"
        b"B@69&etat[init]#B@70&etat[init]#B@71&etat[init]#B@72&etat[init]#"
        b"B@73&etat[init]#B@74&etat[init]#B@75&etat[init]#B@76&etat[init]#"
        b"B@77&etat[ok L]#B@78&etat[ok L]#B@79&etat[init]#B@80&etat[init]}"
    )

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == downstream_payload.decode("utf-8"):
            return SimpleNamespace(
                is_keepalive=False,
                message_type="12",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    try:
        await upstream.send_request(upstream_payload)

        upstream_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
        assert upstream_to_downstream == upstream_payload

        hp_writer.write(downstream_payload)
        await hp_writer.drain()

        upstream_response = await upstream.read_response()
        assert upstream_response == downstream_payload
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_downstream_keepalive_is_forwarded_to_upstream(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keepalive frames from downstream are forwarded unchanged to upstream."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-keepalive{ka#}":
            return SimpleNamespace(
                is_keepalive=True,
                message_type=None,
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()

    try:
        hp_writer.write(b"hp-keepalive{ka#}")
        await hp_writer.drain()

        upstream_payload = await upstream.read_response()
        assert upstream_payload == b"hp-keepalive{ka#}"
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()
        _ = hp_reader


async def test_downstream_keepalive_is_ignored_when_upstream_is_disabled(
    proxy_without_upstream: ProxyServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keepalive frames do not require an upstream handler to be active."""
    proxy = proxy_without_upstream

    assert proxy.upstream_handler is None
    assert proxy.get_last_data() is None

    await proxy._on_downstream_keepalive(b"hp-keepalive{ka#}")  # type: ignore[reportPrivateUsage]

    assert proxy.upstream_handler is None
    assert proxy.get_last_data() is None


async def test_local_command_still_works_when_upstream_configured_but_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-to-downstream flow remains available when upstream connect fails."""
    with socket.socket() as free_socket:
        free_socket.bind(("127.0.0.1", 0))
        unreachable_port = free_socket.getsockname()[1]

    proxy = ProxyServer(
        proxy_host="127.0.0.1",
        proxy_port=0,
        upstream_host="127.0.0.1",
        upstream_port=unreachable_port,
        upstream_enabled=True,
        local_command_host="127.0.0.1",
        local_command_port=0,
        log_downstream=True,
        readuntil_timeout=2,
        log_dir="/tmp",
        log_max_bytes=1024 * 1024,
        log_backup_count=2,
    )
    await proxy.start()

    if proxy.downstream_handler.server and proxy.downstream_handler.server.sockets:
        proxy.proxy_port = proxy.downstream_handler.server.sockets[0].getsockname()[1]
    if proxy.local_command_handler.server and proxy.local_command_handler.server.sockets:
        proxy.local_command_port = proxy.local_command_handler.server.sockets[0].getsockname()[1]

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-local-response{l-upstream-down#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await asyncio.sleep(0.2)

    try:
        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-upstream-down#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-upstream-down#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()
        await proxy.stop()


async def test_local_command_still_works_when_upstream_keepalive_forwarding_fails(
    proxy_with_upstream: tuple[ProxyServer, MockUpstreamEndpoint],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-to-downstream flow continues after upstream keepalive forwarding cannot proceed."""
    proxy, upstream = proxy_with_upstream

    async def fake_decode_text(text_data: str) -> SimpleNamespace:
        if text_data == "hp-keepalive{ka-fail#}":
            return SimpleNamespace(
                is_keepalive=True,
                message_type=None,
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        if text_data == "hp-local-response{l-after-ka-fail#}":
            return SimpleNamespace(
                is_keepalive=False,
                message_type="10",
                timestamp=None,
                groups=[],
                is_old=False,
                count=0,
            )
        raise ValueError(f"Unexpected downstream frame: {text_data}")

    monkeypatch.setattr(decoder, "decode_text", fake_decode_text)

    hp_reader, hp_writer = await asyncio.open_connection(proxy.proxy_host, proxy.proxy_port)
    await upstream.wait_connected()
    await upstream.stop()

    try:
        hp_writer.write(b"hp-keepalive{ka-fail#}")
        await hp_writer.drain()

        local_reader, local_writer = await asyncio.open_connection(
            proxy.local_command_host,
            proxy.local_command_port,
        )
        try:
            local_writer.write(b"cdraminfoDEV1PWD1{501#}")
            await local_writer.drain()

            local_to_downstream = await asyncio.wait_for(hp_reader.readuntil(b"}"), timeout=2.0)
            assert local_to_downstream == b"cdraminfoDEV1PWD1{10#}"

            hp_writer.write(b"hp-local-response{l-after-ka-fail#}")
            await hp_writer.drain()

            local_response = (await asyncio.wait_for(local_reader.read(1024), timeout=2.0)).decode(
                "utf-8",
                errors="ignore",
            )
            assert re.match(
                r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\{l-after-ka-fail#\}$",
                local_response,
            )
        finally:
            local_writer.close()
            await local_writer.wait_closed()
    finally:
        hp_writer.close()
        await hp_writer.wait_closed()


async def test_get_last_data_returns_downstream_last_data(
    proxy_without_upstream: ProxyServer,
) -> None:
    """Proxy get_last_data returns the same object stored by downstream handler."""
    proxy = proxy_without_upstream
    expected_data: dict[str, Any] = {
        "timestamp": "2026-06-25T08:00:00",
        "is_old": False,
        "count": 1,
        "groups": [],
        "raw": "{200#}",
    }

    proxy.downstream_handler.last_data = expected_data

    assert proxy.get_last_data() is expected_data
