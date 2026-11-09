"""Tests for the read-only EG4 Wi-Fi dongle transport seam."""

from __future__ import annotations

import asyncio
import struct
from contextlib import asynccontextmanager, suppress
from datetime import datetime

import pytest

from custom_components.eg4_monitor.local_dongle import (
    DongleFrameError,
    EG4GridBossDongle,
    EG4LocalDongle,
    async_discover_dongles,
    classify_gridboss_transfer,
    build_read_packet,
    compute_crc16,
    parse_dongle_frame,
)


def _fixture_response(
    *,
    dongle_serial: str,
    inverter_serial: str,
    register: int,
    values: list[int],
    response_layout: str = "legacy",
    function: int = 0x04,
) -> bytes:
    """Build a protocol fixture independently of the request builder."""
    if response_layout == "legacy":
        data = bytes([1, function]) + inverter_serial.encode("ascii")
        data += struct.pack("<H", register) + bytes([len(values) * 2])
    else:
        data = bytes([0, 1, function]) + inverter_serial.encode("ascii")
        data += struct.pack("<H", register) + bytes([len(values) * 2])
    data += b"".join(struct.pack("<H", value) for value in values)
    data += struct.pack("<H", compute_crc16(data))
    data_length = len(data)
    return (
        b"\xa1\x1a"
        + struct.pack("<H", 5)
        + struct.pack("<H", 14 + data_length)
        + bytes([1, 0xC2])
        + dongle_serial.encode("ascii")
        + struct.pack("<H", data_length)
        + data
    )


@pytest.mark.asyncio
async def test_local_dongle_discovers_identity_without_writing() -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=0,
                values=[0],
            )
        )
        await writer.drain()
        try:
            requests.append(await asyncio.wait_for(reader.read(4096), timeout=0.05))
        except asyncio.TimeoutError:
            pass
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        identities = await async_discover_dongles(
            ["127.0.0.1"], port=port, timeout=1.0, concurrency=1
        )
    finally:
        server.close()
        await server.wait_closed()

    assert len(identities) == 1
    assert identities[0].dongle_serial == dongle_serial
    assert identities[0].inverter_serial == inverter_serial
    assert requests == [b""]


@pytest.mark.asyncio
async def test_local_dongle_reads_core_and_bms_snapshots() -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"
    requests: list[tuple[int, int]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=0,
                values=[0],
                response_layout="action",
            )
        )
        await writer.drain()
        request = await reader.read(4096)
        register, count = struct.unpack("<HH", request[32:36])
        requests.append((register, count))
        values = [0] * count
        if register == 0:
            values[4] = 523
            values[5] = (100 << 8) | 45
            values[10] = 6725
        elif register == 80:
            values[16] = 5  # BMS parallel count.
            values[17] = 1400  # BMS capacity in Ah.
            values[18] = 2500  # BMS current in 0.01 A.
            values[21] = 3297  # BMS max cell voltage in mV.
            values[22] = 3278  # BMS min cell voltage in mV.
            values[23] = 310  # BMS max cell temperature in 0.1 C.
            values[24] = 290  # BMS min cell temperature in 0.1 C.
            values[26] = 18  # BMS cycle count.
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=register,
                values=values,
            )
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        telemetry = await EG4LocalDongle(
            host="127.0.0.1",
            port=port,
            inverter_serial=None,
        ).async_read_snapshot()
    finally:
        server.close()
        await server.wait_closed()

    assert requests == [(0, 40), (80, 40)]
    assert telemetry.dongle_serial == dongle_serial
    assert telemetry.battery_voltage == 52.3
    assert telemetry.battery_soc == 45.0
    assert telemetry.battery_soh == 100.0
    assert telemetry.charge_power == 6725.0
    assert telemetry.status_code == 0
    assert telemetry.core_registers is not None
    assert len(telemetry.core_registers) == 40
    assert telemetry.bms_registers is not None
    assert len(telemetry.bms_registers) == 40
    assert telemetry.bms_parallel_count == 5
    assert telemetry.bms_capacity_ah == 1400.0
    assert telemetry.bms_current == 25.0
    assert telemetry.bms_max_cell_voltage == 3.297
    assert telemetry.bms_min_cell_voltage == 3.278
    assert telemetry.bms_max_cell_temperature == 31.0
    assert telemetry.bms_min_cell_temperature == 29.0
    assert telemetry.bms_cycle_count == 18.0
    assert telemetry.bms_available is True


def test_gridboss_transfer_classification_identifies_generator_feed() -> None:
    result = classify_gridboss_transfer(
        grid_voltage=2.8,
        grid_frequency=0.0,
        generator_voltage=238.2,
        generator_frequency=60.0,
        generator_power=25000.0,
        ups_voltage=237.3,
    )

    assert result == (
        False,
        True,
        True,
        True,
        "generator_to_ups_candidate",
    )


def test_gridboss_transfer_classification_identifies_source_loss() -> None:
    result = classify_gridboss_transfer(
        grid_voltage=0.0,
        grid_frequency=0.0,
        generator_voltage=0.0,
        generator_frequency=0.0,
        generator_power=0.0,
        ups_voltage=239.0,
    )

    assert result[-1] == "ups_live_source_unknown"


def test_build_read_packet_uses_a11a_envelope_and_modbus_crc() -> None:
    packet = build_read_packet(
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        register=4,
        count=40,
    )

    assert packet[:8] == bytes.fromhex("a11a0100200001c2")
    assert packet[8:18] == b"DJ12345678"
    assert packet[20:22] == bytes([0, 4])
    assert packet[22:32] == b"INV1234567"
    assert packet[32:36] == struct.pack("<HH", 4, 40)
    assert packet[36:38] == struct.pack("<H", compute_crc16(packet[20:36]))


@pytest.mark.asyncio
async def test_gridboss_dongle_reads_input_and_frequency_ranges() -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "4354850310"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=0,
                values=[0],
            )
        )
        await writer.drain()
        request = await reader.read(4096)
        register, count = struct.unpack("<HH", request[32:36])
        values = [0] * count
        if register == 0:
            values[1] = 29
            values[2] = 2373
            values[3] = 2382
            values[26] = 0
            values[27] = 0
            values[30] = 13726
            values[31] = 11324
            values[32] = 6545
            values[33] = 4204
        else:
            values[8] = 6002
            values[9] = 0
            values[10] = 5998
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=register,
                values=values,
            )
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        telemetry = await EG4GridBossDongle(
            host="127.0.0.1", port=port, inverter_serial=inverter_serial
        ).async_read_snapshot()
    finally:
        server.close()
        await server.wait_closed()

    assert telemetry.grid_voltage == 2.9
    assert telemetry.generator_voltage == 238.2
    assert telemetry.generator_power == 25050.0
    assert telemetry.ups_power == 10749.0
    assert telemetry.grid_frequency == 0.0
    assert telemetry.generator_frequency == 59.98
    assert telemetry.transfer_state == "generator_to_ups_candidate"


@pytest.mark.asyncio
async def test_local_dongle_skips_unrelated_unsolicited_response() -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"
    requests: list[tuple[int, int]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=127,
                values=[0],
            )
        )
        await writer.drain()
        request = await reader.read(4096)
        register, count = struct.unpack("<HH", request[32:36])
        requests.append((register, count))
        unrelated = [0] * count
        expected = [0] * count
        expected[4] = 531
        expected[10] = 4200
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=127,
                values=unrelated,
            )
            + _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=register,
                values=expected,
            )
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        telemetry = await EG4LocalDongle(
            host="127.0.0.1", port=port, timeout=1.0
        ).async_read_core_snapshot()
    finally:
        server.close()
        await server.wait_closed()

    assert requests == [(0, 40)]
    assert telemetry.battery_voltage == 53.1
    assert telemetry.charge_power == 4200.0


def test_parse_dongle_frame_decodes_legacy_read_response() -> None:
    frame = _fixture_response(
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        register=4,
        values=[523, 85, 0, 0],
    )

    parsed = parse_dongle_frame(
        frame,
        expected_inverter_serial="INV1234567",
        expected_register=4,
        expected_count=4,
    )

    assert parsed.dongle_serial == "DJ12345678"
    assert parsed.inverter_serial == "INV1234567"
    assert parsed.modbus_function == 4
    assert parsed.register == 4
    assert parsed.values == (523, 85, 0, 0)


@pytest.mark.asyncio
async def test_core_snapshot_rejects_implausible_status_frame() -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=127,
                values=[0],
            )
        )
        await writer.drain()
        request = await reader.read(4096)
        register, count = struct.unpack("<HH", request[32:36])
        values = [0] * count
        values[0] = 0x8200
        values[4] = 531
        values[12] = 2380
        values[15] = 6000
        writer.write(
            _fixture_response(
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                register=register,
                values=values,
            )
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(DongleFrameError, match="invalid inverter status"):
            await EG4LocalDongle(
                host="127.0.0.1", port=port, timeout=1.0
            ).async_read_core_snapshot()
    finally:
        server.close()
        await server.wait_closed()


def test_parse_dongle_frame_decodes_action_byte_response_variant() -> None:
    frame = _fixture_response(
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        register=80,
        values=[109, 660],
        response_layout="action",
    )

    parsed = parse_dongle_frame(
        frame,
        expected_inverter_serial="INV1234567",
        expected_register=80,
        expected_count=2,
    )

    assert parsed.register == 80
    assert parsed.values == (109, 660)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda frame: frame[:-1] + bytes([frame[-1] ^ 0xFF]),
        lambda frame: frame[:8] + b"WRONG12345" + frame[18:],
    ],
)
def test_parse_dongle_frame_rejects_corrupt_or_misrouted_data(mutator) -> None:
    frame = _fixture_response(
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        register=4,
        values=[523],
    )

    with pytest.raises(DongleFrameError):
        parse_dongle_frame(
            mutator(frame),
            expected_dongle_serial="DJ12345678",
            expected_inverter_serial="INV1234567",
            expected_register=4,
            expected_count=1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [EG4LocalDongle, EG4GridBossDongle])
async def test_diagnostics_reject_cached_wrong_function_blocks(client_type) -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(
                _fixture_response(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=0,
                    values=[0],
                )
            )
            await writer.drain()
            request = await reader.readexactly(38)
            function = request[21]
            register, count = struct.unpack("<HH", request[32:36])
            writer.write(
                _fixture_response(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=register,
                    values=[65000] * count,
                    function=3 if function == 4 else 4,
                )
                + _fixture_response(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=register,
                    values=[0] + [123] * (count - 1),
                    function=function,
                )
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        blocks = await client_type(
            host="127.0.0.1", port=port, timeout=1.0
        ).async_read_diagnostics()
    finally:
        server.close()
        await server.wait_closed()

    assert blocks[0]["values"][0] == 0
    for block in blocks:
        assert block["ok"] is True
        assert 65000 not in block["values"]
        assert "error" not in block
        assert datetime.fromisoformat(block["started_at"]).utcoffset().total_seconds() == 0
        assert datetime.fromisoformat(block["completed_at"]) >= datetime.fromisoformat(
            block["started_at"]
        )
        assert block["duration_ms"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unsupported", "timeout"])
async def test_diagnostic_failure_preserves_primary_and_subsequent_block(failure) -> None:
    dongle_serial = "DJ12345678"
    inverter_serial = "INV1234567"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(
                _fixture_response(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=0,
                    values=[0],
                )
            )
            await writer.drain()
            request = await reader.readexactly(38)
            register, count = struct.unpack("<HH", request[32:36])
            if register == 40:
                if failure == "timeout":
                    await reader.read()
                else:
                    # Unsupported firmware replies with an exception, not zero data.
                    writer.write(
                        _fixture_response(
                            dongle_serial=dongle_serial,
                            inverter_serial=inverter_serial,
                            register=register,
                            values=[0],
                            function=0x84,
                        )
                    )
                    await writer.drain()
                return
            values = [0] * count
            if register == 0:
                values[4] = 523
                values[10] = 4200
            writer.write(
                _fixture_response(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=register,
                    values=values,
                    function=request[21],
                )
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = EG4LocalDongle(host="127.0.0.1", port=port, timeout=0.1)
        primary = await client.async_read_core_snapshot()
        blocks = await client.async_read_diagnostics()
    finally:
        server.close()
        await server.wait_closed()

    assert primary.battery_voltage == 52.3
    assert primary.charge_power == 4200.0
    assert blocks[0]["ok"] is False
    assert blocks[0]["values"] is None
    assert blocks[0]["error"]
    assert blocks[0]["duration_ms"] >= 0
    assert blocks[1]["ok"] is True
    assert blocks[1]["values"] == (0,) * blocks[1]["count"]


@pytest.mark.asyncio
async def test_diagnostics_cancellation_closes_active_connection() -> None:
    request_received = asyncio.Event()
    connection_closed = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(
                _fixture_response(
                    dongle_serial="DJ12345678",
                    inverter_serial="INV1234567",
                    register=0,
                    values=[0],
                )
            )
            await writer.drain()
            await reader.readexactly(38)
            request_received.set()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            connection_closed.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    capture = asyncio.create_task(
        EG4LocalDongle(host="127.0.0.1", port=port).async_read_diagnostics()
    )
    try:
        await asyncio.wait_for(request_received.wait(), timeout=1.0)
        capture.cancel()
        with pytest.raises(asyncio.CancelledError):
            await capture
        await asyncio.wait_for(connection_closed.wait(), timeout=1.0)
    finally:
        if not capture.done():
            capture.cancel()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("function", [0, 6, 16, 0x83, 0x84])
def test_read_packet_rejects_non_read_functions(function: int) -> None:
    with pytest.raises(ValueError, match="only Modbus read functions"):
        build_read_packet(
            dongle_serial="DJ12345678",
            inverter_serial="INV1234567",
            register=110,
            count=1,
            function=function,
        )


@asynccontextmanager
async def _evidence_loopback(handle, *, client_type=EG4LocalDongle, timeout=0.2):
    """Exercise the public client over TCP and collect its actual sink records."""
    rows = []
    handlers = set()

    async def record(row):
        rows.append(row)

    async def connection(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await handle(reader, writer, rows)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    client = client_type(
        host="127.0.0.1", port=server.sockets[0].getsockname()[1],
        timeout=timeout, evidence_sink=record, role="master", session_id="test-session",
    )
    try:
        yield client, rows
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(handlers):
            task.cancel()
        await asyncio.gather(*tuple(handlers), return_exceptions=True)


def _identity_response() -> bytes:
    return _fixture_response(
        dongle_serial="DJ12345678", inverter_serial="INV1234567",
        register=127, values=[0],
    )


def _history_response() -> bytes:
    # Genuine preserved native history payload; only its TCP envelope is synthetic.
    payload = bytes.fromhex(
        "a5a519060c0604130000a40999091400d704c904d104c20408000800a600e000"
        "11000b0004000f00a700de0014000800030001000400010004000100bc07870a"
        "0000000000000000c207750a0000000000000000000000000000000000000000"
        "5901000000000000620100000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000c7c8000000000000"
        "010000000000000034ce00000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000000000000007a000c308"
    )
    return (
        b"\xa1\x1a" + struct.pack("<HH", 5, 14 + len(payload))
        + bytes([1, 0xC2]) + b"DJ12345678"
        + struct.pack("<H", len(payload)) + payload
    )


@pytest.mark.asyncio
async def test_history_before_discovery_identity_is_retained_without_requests() -> None:
    requests = []
    request_done = asyncio.Event()

    async def handle(reader, writer, rows):
        writer.write(_history_response() + _identity_response())
        await writer.drain()
        requests.append(await reader.read())
        request_done.set()

    async with _evidence_loopback(handle) as (client, rows):
        identity = await client.async_discover_identity()
        await asyncio.wait_for(request_done.wait(), timeout=1)
        assert identity.inverter_serial == "INV1234567"
        transaction, = rows
        assert transaction["ok"] is True
        assert transaction["request"]["bytes_hex"] is None
        assert [frame["classification"] for frame in transaction["frames"]] == [
            "history", "identity",
        ]
        assert transaction["acquisition_id"]
        assert transaction["transaction_id"]
    assert requests == [b""]


@pytest.mark.asyncio
async def test_history_first_and_nine_unrelated_frames_do_not_hide_reply() -> None:
    async def handle(reader, writer, rows):
        writer.write(_history_response() + _identity_response())
        await writer.drain()
        await reader.readexactly(38)
        values = [0] * 40
        values[4] = 531
        writer.write(
            _identity_response() * 9
            + _fixture_response(
                dongle_serial="DJ12345678", inverter_serial="INV1234567",
                register=0, values=values,
            )
        )
        await writer.drain()

    async with _evidence_loopback(handle) as (client, rows):
        telemetry = await client.async_read_core_snapshot()
        assert telemetry.battery_voltage == 53.1
        transaction, interpretation = rows
        assert len(transaction["frames"]) == 12
        assert transaction["frames"][0]["classification"] == "history"
        assert transaction["frames"][0]["validation"]["crc"] == "passed"
        assert transaction["frames"][0]["values"] is None
        assert all(
            frame["validation"]["range"] == "failed"
            for frame in transaction["frames"][2:-1]
        )
        assert transaction["frames"][-1]["validation"] == {
            "framing": "passed", "crc": "passed", "identity": "passed",
            "function": "passed", "range": "passed",
        }
        assert all(frame["semantic"] == "not_evaluated" for frame in transaction["frames"])
        assert interpretation["semantic"] == "accepted"
        assert interpretation["transaction_id"] == transaction["transaction_id"]
        assert interpretation["started_at"] == transaction["started_at"]
        assert interpretation["completed_at"] == transaction["completed_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_crc,bad_identity", [(True, False), (False, True), (True, True)])
async def test_failed_response_retains_words_and_independent_validation(
    bad_crc, bad_identity,
) -> None:
    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        await reader.readexactly(38)
        values = [0] * 40
        values[4] = 531
        frame = _fixture_response(
            dongle_serial="WRONG12345" if bad_identity else "DJ12345678",
            inverter_serial="INV1234567", register=0, values=values,
        )
        if bad_crc:
            frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
        writer.write(frame)
        await writer.drain()

    async with _evidence_loopback(handle) as (client, rows):
        with pytest.raises(DongleFrameError, match="CRC mismatch|dongle mismatch"):
            await client.async_read_core_snapshot()
        transaction, = rows
        assert transaction["ok"] is False
        frame = transaction["frames"][-1]
        assert frame["values"][4] == 531
        assert frame["validation"] == {
            "framing": "passed",
            "crc": "failed" if bad_crc else "passed",
            "identity": "failed" if bad_identity else "passed",
            "function": "passed", "range": "passed",
        }
        assert frame["semantic"] == "not_evaluated"
        assert transaction["error"]["stage"] == "response"


@pytest.mark.asyncio
async def test_configured_identity_rejection_records_unsolicited_words() -> None:
    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        assert await reader.read() == b""

    async with _evidence_loopback(handle) as (client, rows):
        client.inverter_serial = "OTHER12345"
        with pytest.raises(DongleFrameError, match="serial mismatch"):
            await client.async_discover_identity()
        transaction, = rows
        assert transaction["configured_inverter_serial"] == "OTHER12345"
        assert transaction["frames"][0]["values"] == (0,)
        assert transaction["frames"][0]["validation"]["identity"] == "failed"
        assert transaction["error"]["stage"] == "identity"
        assert transaction["request"]["bytes_hex"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["identity", "response"])
async def test_unsolicited_traffic_has_one_transaction_byte_limit(stage) -> None:
    async def handle(reader, writer, rows):
        if stage == "response":
            writer.write(_identity_response())
            await writer.drain()
            await reader.readexactly(38)
        writer.write(_history_response() * 1000)
        await writer.drain()
        await reader.read()

    async with _evidence_loopback(handle, timeout=2.0) as (client, rows):
        with pytest.raises(DongleFrameError, match="received-traffic limit"):
            await client.async_read_core_snapshot()
        transaction, = rows
        assert transaction["received_bytes"] == 256 * 1024
        assert sum(len(bytes.fromhex(frame["bytes_hex"])) for frame in transaction["frames"]) == 256 * 1024
        assert transaction["truncated"] is True
        assert transaction["ok"] is False
        assert transaction["error"]["stage"] == stage


@pytest.mark.asyncio
async def test_oversized_frame_retains_header_and_reports_truncation() -> None:
    header = b"\xa1\x1a\x01\x00\xff\xff"

    async def handle(reader, writer, rows):
        writer.write(header)
        await writer.drain()
        await reader.read()

    async with _evidence_loopback(handle) as (client, rows):
        with pytest.raises(DongleFrameError, match="oversized"):
            await client.async_read_core_snapshot()
        transaction, = rows
        assert transaction["frames"][0]["bytes_hex"] == header.hex()
        assert transaction["frames"][0]["validation"]["framing"] == "failed"
        assert transaction["received_bytes"] == 6
        assert transaction["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["eof", "timeout", "cancel"])
@pytest.mark.parametrize("length", [3, 42])
async def test_interrupted_response_preserves_partial_bytes(failure, length) -> None:
    sent = asyncio.Event()
    partial = _fixture_response(
        dongle_serial="DJ12345678", inverter_serial="INV1234567",
        register=0, values=[0, 531] + [0] * 38,
    )[:length]

    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        await reader.readexactly(38)
        writer.write(partial)
        await writer.drain()
        sent.set()
        if failure != "eof":
            await reader.read()

    async with _evidence_loopback(handle) as (client, rows):
        task = asyncio.create_task(client.async_read_core_snapshot())
        await asyncio.wait_for(sent.wait(), timeout=1)
        if failure == "cancel":
            # Give the transport a scheduling turn to consume the delivered bytes.
            await asyncio.sleep(0.01)
            task.cancel()
        error = {
            "eof": asyncio.IncompleteReadError,
            "timeout": TimeoutError,
            "cancel": asyncio.CancelledError,
        }[failure]
        with pytest.raises(error):
            await task
        transaction, = rows
        frame = transaction["frames"][-1]
        assert frame["bytes_hex"] == partial.hex()
        assert frame["validation"]["framing"] == "failed"
        assert frame["validation"]["crc"] == "not_evaluated"
        assert transaction["ok"] is False
        assert transaction["truncated"] is True
        assert transaction["error"]["type"] == error.__name__
        assert transaction["received_bytes"] == len(_identity_response()) + length
        if length == 42:
            assert frame["values"] == (0, 531, 0)


@pytest.mark.asyncio
async def test_deadline_includes_identity_wait_and_requested_response() -> None:
    async def handle(reader, writer, rows):
        await asyncio.sleep(0.08)
        writer.write(_identity_response())
        await writer.drain()
        await reader.readexactly(38)
        await asyncio.sleep(0.08)
        writer.write(_fixture_response(
            dongle_serial="DJ12345678", inverter_serial="INV1234567",
            register=0, values=[0] * 40,
        ))
        await writer.drain()

    async with _evidence_loopback(handle, timeout=0.12) as (client, rows):
        with pytest.raises(TimeoutError):
            await client.async_read_core_snapshot()
        transaction, = rows
        assert transaction["error"]["stage"] == "response"
        assert len(transaction["frames"]) == 1
        assert transaction["request"]["bytes_hex"] is not None


@pytest.mark.asyncio
async def test_capture_groups_wait_for_reads_and_serializes_same_client() -> None:
    active = 0
    peak = 0
    acquired = []

    async def handle(reader, writer, rows):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            writer.write(_identity_response())
            await writer.drain()
            request = await reader.readexactly(38)
            register, count = struct.unpack("<HH", request[32:36])
            acquired.append(register)
            # Prior transaction must be queued before this request starts.
            assert len([row for row in rows if row["kind"] == "transaction"]) == len(acquired) - 1
            await asyncio.sleep(0.01)
            writer.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial="INV1234567",
                register=register, values=[0] * count, function=request[21],
            ))
            await writer.drain()
        finally:
            active -= 1
        await reader.read()

    async with _evidence_loopback(handle, timeout=1) as (client, rows):
        with client.capture() as acquisition:
            await asyncio.gather(
                asyncio.wait_for(client.async_read_core_snapshot(), timeout=1),
                asyncio.wait_for(client.async_read_bms_snapshot(), timeout=1),
            )
            await asyncio.wait_for(client.async_read_diagnostics(), timeout=1)
        transactions = [row for row in rows if row["kind"] == "transaction"]
        assert len(transactions) == 5
        assert peak == 1
        assert {row["acquisition_id"] for row in rows} == {acquisition["acquisition_id"]}
        assert acquisition["transaction_ids"] == [row["transaction_id"] for row in transactions]
        assert acquisition["successful_transaction_ids"] == acquisition["transaction_ids"]
        assert acquisition["failed_transaction_ids"] == []


@pytest.mark.asyncio
async def test_public_snapshot_assigns_one_acquisition_without_explicit_capture() -> None:
    async def handle(reader, writer, rows):
        writer.write(_identity_response())
        await writer.drain()
        request = await reader.readexactly(38)
        register, count = struct.unpack("<HH", request[32:36])
        writer.write(_fixture_response(
            dongle_serial="DJ12345678", inverter_serial="INV1234567",
            register=register, values=[0] * count,
        ))
        await writer.drain()

    async with _evidence_loopback(handle) as (client, rows):
        await client.async_read_snapshot()
        await client.async_read_snapshot()
        transactions = [row for row in rows if row["kind"] == "transaction"]
        assert len(transactions) == 4
        first, second, third, fourth = transactions
        assert first["acquisition_id"] == second["acquisition_id"]
        assert third["acquisition_id"] == fourth["acquisition_id"]
        assert first["acquisition_id"] != third["acquisition_id"]
        assert len({row["transaction_id"] for row in transactions}) == 4
        assert all(row["role"] == "master" for row in rows)
        assert all(row["session_id"] == "test-session" for row in rows)
