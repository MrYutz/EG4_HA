"""Acquired evidence survives semantic rejection at the TCP/filesystem seam."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.eg4_monitor.local_dongle import (
    DongleFrameError,
    EG4GridBossDongle,
    EG4LocalDongle,
)
from custom_components.eg4_monitor.local_dongle_trace import LocalDongleTraceWriter
from tests.test_local_dongle import (
    _evidence_loopback,
    _fixture_response,
    _identity_response,
)


@pytest.mark.asyncio
async def test_rejected_status_keeps_received_words_on_disk(tmp_path) -> None:
    async def handle(reader, writer):
        try:
            writer.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial="INV1234567",
                register=127, values=[0],
            ))
            await writer.drain()
            await reader.readexactly(38)
            values = [0] * 40
            values[0] = 33280
            writer.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial="INV1234567",
                register=0, values=values,
            ))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    recorder = LocalDongleTraceWriter(tmp_path)
    try:
        client = EG4LocalDongle(
            host="127.0.0.1", port=server.sockets[0].getsockname()[1],
            evidence_sink=recorder.async_write, role="master", session_id="test-session",
        )
        with pytest.raises(DongleFrameError, match="invalid inverter status"):
            await client.async_read_core_snapshot()
    finally:
        await recorder.async_close()
        server.close()
        await server.wait_closed()

    rows = [json.loads(line) for line in recorder.active_path.read_text().splitlines()]
    transaction = next(row for row in rows if row.get("kind") == "transaction")
    interpretation = next(row for row in rows if row.get("kind") == "interpretation")
    assert transaction["frames"][-1]["values"][0] == 33280
    assert transaction["ok"] is True
    assert interpretation["transaction_id"] == transaction["transaction_id"]
    assert interpretation["semantic"] == "rejected"
    assert "33280" in interpretation["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("voltage_index", [12, 20])
async def test_live_voltage_invalid_frequency_remains_rejected_on_disk(
    tmp_path, voltage_index,
) -> None:
    values = [0] * 40
    values[voltage_index] = 2390

    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        await reader.readexactly(38)
        writer.write(_fixture_response(
            dongle_serial="DJ12345678", inverter_serial="INV1234567",
            register=0, values=values,
        ))
        await writer.drain()

    recorder = LocalDongleTraceWriter(tmp_path)
    try:
        async with _evidence_loopback(handle) as (client, _):
            client.evidence_sink = recorder.async_write
            with pytest.raises(DongleFrameError, match="frequency for live voltage"):
                await client.async_read_core_snapshot()
    finally:
        await recorder.async_close()

    rows = [json.loads(line) for line in recorder.active_path.read_text().splitlines()]
    transaction, interpretation = rows
    assert transaction["ok"] is True
    assert transaction["frames"][-1]["values"] == values
    assert interpretation["semantic"] == "rejected"
    assert interpretation["transaction_id"] == transaction["transaction_id"]
    assert interpretation["started_at"] == transaction["started_at"]
    assert interpretation["completed_at"] == transaction["completed_at"]


@pytest.mark.asyncio
async def test_gridboss_input_is_persisted_when_frequency_times_out(tmp_path) -> None:
    input_values = [0] * 40
    input_values[2] = 2390
    input_values[3] = 2400
    input_values[30] = 1200
    input_values[31] = 1300
    recorder = LocalDongleTraceWriter(tmp_path)
    queued = []

    async def record(row):
        queued.append(row)
        await recorder.async_write(row)

    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        request = await reader.readexactly(38)
        register = int.from_bytes(request[32:34], "little")
        if register == 0:
            writer.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial="INV1234567",
                register=0, values=input_values,
            ))
            await writer.drain()
        else:
            assert register == 120
            assert len(queued) == 1
            assert queued[0]["ok"] is True
            assert queued[0]["frames"][-1]["values"] == tuple(input_values)
            await reader.read()

    try:
        async with _evidence_loopback(
            handle, client_type=EG4GridBossDongle, timeout=0.1,
        ) as (client, _):
            client.evidence_sink = record
            with client.capture() as acquisition:
                with pytest.raises(TimeoutError):
                    await client.async_read_snapshot()
    finally:
        await recorder.async_close()

    rows = [json.loads(line) for line in recorder.active_path.read_text().splitlines()]
    input_transaction, frequency_transaction = rows
    assert input_transaction["kind"] == "transaction"
    assert input_transaction["frames"][-1]["values"] == input_values
    assert input_transaction["ok"] is True
    assert frequency_transaction["request"]["register"] == 120
    assert frequency_transaction["ok"] is False
    assert frequency_transaction["error"]["stage"] == "response"
    assert frequency_transaction["error"]["type"] == "TimeoutError"
    assert frequency_transaction["acquisition_id"] == input_transaction["acquisition_id"]
    assert acquisition["successful_transaction_ids"] == [input_transaction["transaction_id"]]
    assert acquisition["failed_transaction_ids"] == [frequency_transaction["transaction_id"]]
