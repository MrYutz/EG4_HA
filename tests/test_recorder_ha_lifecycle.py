"""Actual HA lifecycle with external HTTP/TCP fixtures and persisted evidence."""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from yarl import URL

from tests.test_local_recorder import _endpoint, _words


@pytest.mark.asyncio
async def test_core_stop_drains_real_recorder_and_persists_terminal_health(tmp_path):
    """Core stop is not entry unload; accepted capture still needs a durable tail."""
    source = Path(__file__).resolve().parents[1] / "custom_components"
    (tmp_path / "custom_components").symlink_to(source, target_is_directory=True)

    async def portal(request):
        """Serve actual login/discovery responses and an unavailable runtime."""
        if request.path.endswith("/web/login"):
            if request.method == "POST":
                response = web.HTTPFound("/WManage/web/monitor/inverter")
                response.set_cookie("JSESSIONID", "synthetic-cookie")
                raise response
            return web.Response(text="<html><form>synthetic login</form></html>")
        if request.path.endswith("/web/monitor/inverter"):
            return web.Response(text="<html>Welcome synthetic monitor logout</html>")
        if request.path.endswith("/web/config/plant/list/viewer"):
            return web.json_response({"rows": [{"id": 1, "name": "Synthetic stop"}]})
        if request.path.endswith("/web/config/inverter/list"):
            return web.json_response({"rows": [{"serialNum": "INV1234567", "deviceTypeText": "FlexBOSS"}]})
        return web.Response(status=503, text="synthetic portal runtime unavailable")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", portal)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    original_request = aiohttp.ClientSession._request
    original_resolve = aiohttp.TCPConnector._resolve_host

    async def local_request(client, method, url, **kwargs):
        """Redirect vendor traffic at the external HTTP boundary, not owned APIs."""
        url = URL(url)
        if url.host == "monitor.eg4electronics.com":
            url = url.with_scheme("http").with_port(port)
        elif url.host not in ("127.0.0.1", "localhost", None):
            raise AssertionError(f"unexpected fixture network destination: {url.host}")
        return await original_request(client, method, url, **kwargs)

    async def local_resolve(connector, host, requested_port, *args, **kwargs):
        """Preserve the real cookie origin while resolving only to loopback."""
        if host == "monitor.eg4electronics.com":
            return [{"hostname": host, "host": "127.0.0.1", "port": requested_port,
                     "family": socket.AF_INET, "proto": socket.IPPROTO_TCP, "flags": 0}]
        return await original_resolve(connector, host, requested_port, *args, **kwargs)

    async def respond(shape):
        """Return actual framed register words through the existing TCP server."""
        return _words(shape)

    hass = None
    data = None
    try:
        with patch.object(aiohttp.ClientSession, "_request", local_request), patch.object(
            aiohttp.TCPConnector, "_resolve_host", local_resolve,
        ):
            async with _endpoint(respond) as (device, _, _):
                hass = HomeAssistant(str(tmp_path))
                loader.async_setup(hass)
                hass.config.skip_pip = True
                await async_from_config_dict(
                    {"homeassistant": {"name": "Synthetic stop", "time_zone": "UTC"}}, hass,
                )
                await hass.async_start()
                entry = ConfigEntry(
                    data={"username": "synthetic", "password": "synthetic"},
                    options={"master_serial": "INV1234567", "local_master_host": "127.0.0.1",
                             "local_auto_discovery": False, "local_dongle_port": device.port,
                             "local_trace_enabled": True, "local_trace_interval": 1},
                    discovery_keys=MappingProxyType({}), subentries_data=[], domain="eg4_monitor",
                    entry_id="synthetic-ha-stop", title="Synthetic stop", unique_id=None,
                    source="user", version=1, minor_version=1,
                )
                await hass.config_entries.async_add(entry)
                assert entry.state.value == "loaded"
                data = hass.data["eg4_monitor"][entry.entry_id]
                recorder = data["local_recorder"]
                raw = data["local_trace_writer"]
                async with asyncio.timeout(10):
                    while not recorder.snapshot().inverters:
                        await asyncio.sleep(0.02)
                await raw.async_sync()
                await hass.async_stop()

                def saved_records():
                    """Read real persisted rows after the framework stop returned."""
                    return [json.loads(line) for path in raw.root.glob("local-dongle*.jsonl")
                            for line in path.read_text().splitlines()]

                rows = await asyncio.to_thread(saved_records)
                shutdown = [row for row in rows if row.get("kind") == "recorder_health"
                            and row.get("reason") == "shutdown"]
                assert len(shutdown) == 1
                assert shutdown[0]["health"]["Roles"]["flexboss_master"]["PrimaryAvailable"] is False
                assert raw.health["QueueDepth"] == 0
                assert data["controller_trace_writer"].health["QueueDepth"] == 0
                assert recorder.snapshot().health["Roles"]["flexboss_master"]["PrimaryAvailable"] is False
    finally:
        if data is not None:
            await data["local_recorder"].async_close()
            for key in ("local_trace_writer", "controller_trace_writer"):
                if data.get(key) is not None:
                    await data[key].async_close()
        if hass is not None:
            await hass.async_stop()
        await runner.cleanup()
