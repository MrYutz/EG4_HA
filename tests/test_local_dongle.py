"""Tests for the read-only EG4 Wi-Fi dongle transport seam."""

from __future__ import annotations

import asyncio
import struct
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
