"""Read-only local transport for EG4/LuxPower Wi-Fi dongles."""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4
from weakref import WeakValueDictionary

from .const import LOCAL_DONGLE_TRACE_SCHEMA

PACKET_PREFIX = b"\xa1\x1a"
PROTOCOL_VERSION = 1
TCP_FUNC_TRANSLATED = 0xC2
MODBUS_READ_HOLDING = 0x03
MODBUS_READ_INPUT = 0x04
_CORE_REGISTER = 0
_CORE_COUNT = 40
_BMS_REGISTER = 80
_BMS_COUNT = 40
_GRIDBOSS_REGISTER = 0
_GRIDBOSS_COUNT = 40
_GRIDBOSS_FREQUENCY_REGISTER = 120
_GRIDBOSS_FREQUENCY_COUNT = 12
_MAX_PACKET_SIZE = 4096
_MAX_TRANSACTION_BYTES = 256 * 1024
_ROUTE_REQUEST_LOCKS: WeakValueDictionary[tuple[str, int], asyncio.Lock] = WeakValueDictionary()
_Decoded = TypeVar("_Decoded")
_VALID_STATUS_CODES = frozenset(
    {0x00, 0x04, 0x08, 0x0C, 0x10, 0x11, 0x14, 0x20, 0x28, 0x40, 0x80, 0xC0}
)


class DongleFrameError(ValueError):
    """Raised when a local dongle frame is malformed or misrouted."""


def compute_crc16(data: bytes) -> int:
    """Compute the CRC-16/Modbus checksum used by the embedded frame."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _serial_bytes(serial: str) -> bytes:
    """Encode a protocol serial, padding the fixed ten-byte field."""
    encoded = serial.encode("ascii")
    if len(encoded) > 10:
        raise ValueError("EG4 dongle and inverter serials must be at most 10 bytes")
    return encoded.ljust(10, b"\x00")


def build_read_packet(
    *,
    dongle_serial: str,
    inverter_serial: str,
    register: int,
    count: int,
    function: int = MODBUS_READ_INPUT,
) -> bytes:
    """Build an FC03/FC04 read request for the A1/1A TCP envelope."""
    if function not in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT):
        raise ValueError("only Modbus read functions 03 and 04 are supported")
    if not 0 <= register <= 0xFFFF:
        raise ValueError("register must fit an unsigned 16-bit field")
    if not 1 <= count <= 125:
        raise ValueError("count must be between 1 and 125 registers")

    data = bytes([0, function]) + _serial_bytes(inverter_serial)
    data += struct.pack("<HH", register, count)
    data += struct.pack("<H", compute_crc16(data))
    data_length = len(data)
    frame_length = 14 + data_length
    return (
        PACKET_PREFIX
        + struct.pack("<HH", PROTOCOL_VERSION, frame_length)
        + bytes([1, TCP_FUNC_TRANSLATED])
        + _serial_bytes(dongle_serial)
        + struct.pack("<H", data_length)
        + data
    )


@dataclass(frozen=True)
class DongleFrame:
    """Validated read response from a local EG4 dongle."""

    version: int
    tcp_function: int
    dongle_serial: str
    inverter_serial: str
    modbus_function: int
    register: int
    values: tuple[int, ...]


@dataclass(frozen=True)
class EG4LocalDongleIdentity:
    """Identity announced by a local dongle before any register request."""

    host: str
    port: int
    dongle_serial: str
    inverter_serial: str


@dataclass(frozen=True)
class EG4LocalTelemetry:
    """Read-only local inverter telemetry and BMS availability diagnostics."""

    host: str
    port: int
    dongle_serial: str
    inverter_serial: str
    battery_voltage: float | None
    battery_soc: float | None
    battery_soh: float | None
    charge_power: float | None
    discharge_power: float | None
    bms_parallel_count: int | None
    bms_capacity_ah: float | None
    bms_current: float | None
    bms_max_cell_voltage: float | None
    bms_min_cell_voltage: float | None
    bms_max_cell_temperature: float | None
    bms_min_cell_temperature: float | None
    bms_cycle_count: float | None
    bms_available: bool
    status_code: int | None = None
    grid_voltage: float | None = None
    grid_frequency: float | None = None
    inverter_power: float | None = None
    rectifier_power: float | None = None
    eps_voltage_r: float | None = None
    eps_voltage_s: float | None = None
    eps_voltage_t: float | None = None
    eps_frequency: float | None = None
    eps_power: float | None = None
    grid_export_power: float | None = None
    grid_import_power: float | None = None
    core_registers: tuple[int, ...] | None = None
    bms_registers: tuple[int, ...] | None = None


@dataclass(frozen=True)
class EG4GridBossTelemetry:
    """Read-only GridBOSS measurements used to correlate transfer events.

    The GridBOSS exposes these values as input registers (function 0x04).  The
    raw register tuples are retained so a future mapping correction cannot
    destroy the evidence captured during a transfer event.
    """

    host: str
    port: int
    dongle_serial: str
    inverter_serial: str
    grid_voltage: float | None
    ups_voltage: float | None
    generator_voltage: float | None
    grid_l1_voltage: float | None
    grid_l2_voltage: float | None
    ups_l1_voltage: float | None
    ups_l2_voltage: float | None
    generator_l1_voltage: float | None
    generator_l2_voltage: float | None
    grid_l1_current: float | None
    grid_l2_current: float | None
    ups_l1_current: float | None
    ups_l2_current: float | None
    generator_l1_current: float | None
    generator_l2_current: float | None
    grid_power: float | None
    load_power: float | None
    ups_power: float | None
    generator_power: float | None
    grid_l1_power: float | None
    grid_l2_power: float | None
    load_l1_power: float | None
    load_l2_power: float | None
    ups_l1_power: float | None
    ups_l2_power: float | None
    generator_l1_power: float | None
    generator_l2_power: float | None
    phase_lock_frequency: float | None
    grid_frequency: float | None
    generator_frequency: float | None
    grid_available: bool
    generator_available: bool
    generator_supplying: bool
    ups_live: bool
    transfer_state: str
    input_registers: tuple[int, ...]
    frequency_registers: tuple[int, ...]
    input_registers_valid: bool = True


@dataclass(frozen=True)
class EG4LocalBMSData:
    """Decoded BMS range that can be cached between core polls."""

    dongle_serial: str
    inverter_serial: str
    parallel_count: int | None
    capacity_ah: float | None
    current: float | None
    max_cell_voltage: float | None
    min_cell_voltage: float | None
    max_cell_temperature: float | None
    min_cell_temperature: float | None
    cycle_count: float | None
    available: bool
    registers: tuple[int, ...]


def _decode_ascii_serial(value: bytes) -> str:
    """Decode a fixed-width ASCII serial without padding NULs."""
    return value.decode("ascii", errors="replace").rstrip("\x00")


def parse_dongle_frame(
    frame: bytes,
    *,
    expected_dongle_serial: str | None = None,
    expected_inverter_serial: str | None = None,
    expected_register: int | None = None,
    expected_count: int | None = None,
    expected_function: int | None = None,
) -> DongleFrame:
    """Validate and decode a read response from either known frame layout.

    Current EG4 dongles in the installation return ``address, function, ...``;
    newer pylxpweb-compatible firmware can include an additional action byte.
    Both layouts are read-only and are accepted here, but serial/register/
    function mismatches are rejected rather than exposed as telemetry.
    """
    if expected_function is not None and expected_function not in (
        MODBUS_READ_HOLDING,
        MODBUS_READ_INPUT,
    ):
        raise ValueError("only Modbus read functions 03 and 04 are supported")
    if len(frame) < 22 or frame[:2] != PACKET_PREFIX:
        raise DongleFrameError("invalid A1/1A frame prefix or length")

    version = struct.unpack("<H", frame[2:4])[0]
    advertised_length = struct.unpack("<H", frame[4:6])[0]
    packet_size = 6 + advertised_length
    if packet_size > _MAX_PACKET_SIZE or packet_size > len(frame):
        raise DongleFrameError("truncated or oversized advertised frame")

    data_length = struct.unpack("<H", frame[18:20])[0]
    if advertised_length != 14 + data_length:
        raise DongleFrameError("outer and inner frame lengths disagree")
    if data_length < 2:
        raise DongleFrameError("embedded frame has no CRC")

    packet = frame[:packet_size]
    data_end = 20 + data_length - 2
    data = packet[20:data_end]
    received_crc = struct.unpack("<H", packet[data_end : data_end + 2])[0]
    if compute_crc16(data) != received_crc:
        raise DongleFrameError("embedded Modbus CRC mismatch")

    tcp_function = packet[7]
    if tcp_function != TCP_FUNC_TRANSLATED:
        raise DongleFrameError(f"unexpected TCP function 0x{tcp_function:02x}")
    dongle_serial = _decode_ascii_serial(packet[8:18])
    if expected_dongle_serial is not None and dongle_serial != expected_dongle_serial:
        raise DongleFrameError(
            f"response dongle mismatch: expected {expected_dongle_serial}, got {dongle_serial}"
        )

    # Layout A: address + function + serial + register + byte_count + data.
    if len(data) >= 15 and data[1] in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT):
        function_offset = 1
        serial_offset = 2
        register_offset = 12
        count_offset = 14
        values_offset = 15
    # Layout B: action + address + function + serial + register + byte_count.
    elif len(data) >= 16 and data[2] in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT):
        function_offset = 2
        serial_offset = 3
        register_offset = 13
        count_offset = 15
        values_offset = 16
    else:
        raise DongleFrameError("unsupported embedded read-response layout")

    modbus_function = data[function_offset]
    if expected_function is not None and modbus_function != expected_function:
        raise DongleFrameError(
            f"response function mismatch: expected {expected_function}, got {modbus_function}"
        )
    inverter_serial = _decode_ascii_serial(data[serial_offset : serial_offset + 10])
    register = struct.unpack("<H", data[register_offset : register_offset + 2])[0]
    byte_count = data[count_offset]
    raw_values = data[values_offset : values_offset + byte_count]
    if len(raw_values) != byte_count or byte_count % 2:
        raise DongleFrameError("embedded register payload is truncated or odd-sized")
    values = tuple(
        struct.unpack("<H", raw_values[index : index + 2])[0]
        for index in range(0, byte_count, 2)
    )

    if expected_inverter_serial is not None and inverter_serial != expected_inverter_serial:
        raise DongleFrameError(
            f"response serial mismatch: expected {expected_inverter_serial}, got {inverter_serial}"
        )
    if expected_register is not None and register != expected_register:
        raise DongleFrameError(
            f"response register mismatch: expected {expected_register}, got {register}"
        )
    if expected_count is not None and len(values) != expected_count:
        raise DongleFrameError(
            f"register count mismatch: expected {expected_count}, got {len(values)}"
        )

    return DongleFrame(
        version=version,
        tcp_function=tcp_function,
        dongle_serial=dongle_serial,
        inverter_serial=inverter_serial,
        modbus_function=modbus_function,
        register=register,
        values=values,
    )


def _frame_evidence(
    raw: bytes,
    *,
    dongle_serial: str | None,
    inverter_serial: str | None,
    function: int | None,
    register: int | None,
    count: int | None,
) -> dict[str, Any]:
    """Inspect obtainable fields independently; never authorize sensor use."""
    validation = dict.fromkeys(
        ("framing", "crc", "identity", "function", "range"), "not_evaluated"
    )
    evidence: dict[str, Any] = {
        "bytes_hex": raw.hex(),
        "values": None,
        "validation": validation,
        "semantic": "not_evaluated",
        "classification": "partial",
    }
    if len(raw) < 6:
        validation["framing"] = "failed"
        return evidence
    packet_size = 6 + struct.unpack_from("<H", raw, 4)[0]
    framed = (
        raw[:2] == PACKET_PREFIX
        and 22 <= packet_size <= _MAX_PACKET_SIZE
        and len(raw) == packet_size
    )
    data_length = struct.unpack_from("<H", raw, 18)[0] if len(raw) >= 20 else 0
    framed = framed and packet_size == 20 + data_length and data_length >= 2
    validation["framing"] = "passed" if framed else "failed"
    if len(raw) >= 18:
        evidence["observed_dongle_serial"] = _decode_ascii_serial(raw[8:18])
    if len(raw) < 20:
        return evidence
    if data_length >= 2 and len(raw) >= 20 + data_length:
        data_end = 20 + data_length - 2
        validation["crc"] = (
            "passed"
            if compute_crc16(raw[20:data_end]) == struct.unpack_from("<H", raw, data_end)[0]
            else "failed"
        )
    if raw[7] != TCP_FUNC_TRANSLATED or (
        data_length == 256 and raw[20:22] == b"\xa5\xa5"
    ):
        validation["function"] = "failed"
        evidence["classification"] = "history"
        return evidence
    # Layout inspection is deliberately independent of CRC/identity validity.
    data = raw[20 : min(len(raw), 20 + max(0, data_length - 2))]
    if len(data) >= 15 and data[1] in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT):
        offset = 1
    elif len(data) >= 16 and data[2] in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT):
        offset = 2
    elif data[:1] == b"\x01":
        offset = 1
    elif data[:2] == b"\x00\x01":
        offset = 2
    else:
        return evidence
    if len(data) < offset + 14:
        return evidence
    observed_dongle = _decode_ascii_serial(raw[8:18])
    observed_inverter = _decode_ascii_serial(data[offset + 1 : offset + 11])
    evidence["observed_inverter_serial"] = observed_inverter
    validation["identity"] = (
        "passed"
        if observed_dongle
        and observed_inverter
        and (dongle_serial is None or observed_dongle == dongle_serial)
        and (inverter_serial is None or observed_inverter == inverter_serial)
        else "failed"
    )
    observed_function = data[offset]
    validation["function"] = (
        "passed"
        if observed_function in (MODBUS_READ_HOLDING, MODBUS_READ_INPUT)
        and (function is None or observed_function == function)
        else "failed"
    )
    observed_register = struct.unpack_from("<H", data, offset + 11)[0]
    byte_count = data[offset + 13]
    payload = data[offset + 14 : offset + 14 + byte_count]
    evidence["values"] = tuple(
        struct.unpack_from("<H", payload, index)[0]
        for index in range(0, len(payload) - 1, 2)
    )
    if register is not None or count is not None:
        validation["range"] = (
            "passed"
            if (register is None or observed_register == register)
            and (count is None or byte_count == 2 * count)
            and len(payload) == byte_count
            and byte_count % 2 == 0
            else "failed"
        )
    evidence["classification"] = "unrelated"
    return evidence


class _DongleTrafficLimitError(DongleFrameError):
    """The transaction exhausted its bounded received-traffic allowance."""


async def _read_frame(
    reader: asyncio.StreamReader,
    transaction: dict[str, Any],
    *,
    dongle_serial: str | None,
    inverter_serial: str | None,
    function: int | None,
    register: int | None,
    count: int | None,
) -> bytes:
    """Retain every consumed byte, including interrupted headers and payloads."""
    raw = bytearray()

    async def receive(size: int) -> None:
        while size:
            remaining = _MAX_TRANSACTION_BYTES - transaction["received_bytes"]
            if remaining <= 0:
                transaction["truncated"] = True
                raise _DongleTrafficLimitError("transaction received-traffic limit exhausted")
            chunk = await reader.read(min(size, remaining))
            if not chunk:
                raise asyncio.IncompleteReadError(bytes(raw), len(raw) + size)
            raw.extend(chunk)
            transaction["received_bytes"] += len(chunk)
            size -= len(chunk)

    try:
        await receive(6)
        if raw[:2] != PACKET_PREFIX:
            raise DongleFrameError("stream did not start with an A1/1A frame")
        advertised_length = struct.unpack_from("<H", raw, 4)[0]
        if 6 + advertised_length > _MAX_PACKET_SIZE:
            transaction["truncated"] = True
            raise DongleFrameError("dongle advertised an oversized frame")
        await receive(advertised_length)
        return bytes(raw)
    finally:
        if raw:
            evidence = _frame_evidence(
                bytes(raw),
                dongle_serial=dongle_serial,
                inverter_serial=inverter_serial,
                function=function,
                register=register,
                count=count,
            )
            transaction["frames"].append(evidence)
            for field in ("observed_dongle_serial", "observed_inverter_serial"):
                if field in evidence:
                    transaction[field] = evidence[field]
            if evidence["validation"]["framing"] == "failed":
                transaction["truncated"] = True


def _bounded(value: float | int | None, lower: float, upper: float) -> float | None:
    """Return a telemetry value only when it is inside its physical range."""
    if value is None:
        return None
    numeric = float(value)
    return numeric if lower <= numeric <= upper else None


def _signed_word(value: int) -> int:
    """Decode one 16-bit Modbus register as a signed integer."""
    return struct.unpack("<h", struct.pack("<H", value))[0]


def _gridboss_register_value(
    values: tuple[int, ...], register: int
) -> int | None:
    """Return a one-based GridBOSS register value from a zero-based range."""
    index = register - _GRIDBOSS_REGISTER
    if index < 0 or index >= len(values):
        return None
    return values[index]


def _gridboss_voltage(values: tuple[int, ...], register: int) -> float | None:
    """Decode a GridBOSS voltage register in tenths of a volt."""
    raw = _gridboss_register_value(values, register)
    if raw is None:
        return None
    # A short-lived dongle response can contain a non-measurement/status word
    # in place of the voltage block. Preserve it as unavailable rather than
    # publishing a false voltage (e.g. raw 0xA000 -> 4096 V).
    return _bounded(raw / 10.0, 0.0, 500.0)


def _gridboss_current(values: tuple[int, ...], register: int) -> float | None:
    """Decode a GridBOSS current register in tenths of an ampere."""
    raw = _gridboss_register_value(values, register)
    return _bounded(
        _signed_word(raw) / 10.0 if raw is not None else None,
        -1_000.0,
        1_000.0,
    )


def _gridboss_power(values: tuple[int, ...], register: int) -> float | None:
    """Decode a signed GridBOSS power register in watts."""
    raw = _gridboss_register_value(values, register)
    return _bounded(
        float(_signed_word(raw)) if raw is not None else None,
        -100_000.0,
        100_000.0,
    )


def _gridboss_frequency(
    values: tuple[int, ...], register: int, start_register: int
) -> float | None:
    """Decode a GridBOSS frequency register in hundredths of a hertz."""
    index = register - start_register
    if index < 0 or index >= len(values):
        return None
    return _bounded(values[index] / 100.0, 0.0, 100.0)


def classify_gridboss_transfer(
    *,
    grid_voltage: float | None,
    grid_frequency: float | None,
    generator_voltage: float | None,
    generator_frequency: float | None,
    generator_power: float | None,
    ups_voltage: float | None,
    minimum_voltage: float = 80.0,
    minimum_frequency: float = 45.0,
    minimum_power: float = 100.0,
) -> tuple[bool, bool, bool, bool, str]:
    """Classify measured source/output combinations without claiming relay state.

    The GridBOSS does not expose a documented transfer-relay contact in this
    range.  The returned state therefore describes what its terminals show;
    ``*_to_ups_candidate`` is deliberately labelled as an inference.
    """
    grid_available = bool(
        grid_voltage is not None
        and grid_voltage >= minimum_voltage
        and grid_frequency is not None
        and grid_frequency >= minimum_frequency
    )
    generator_available = bool(
        generator_voltage is not None
        and generator_voltage >= minimum_voltage
        and generator_frequency is not None
        and generator_frequency >= minimum_frequency
    )
    generator_supplying = bool(
        generator_available
        and generator_power is not None
        and abs(generator_power) >= minimum_power
    )
    ups_live = bool(ups_voltage is not None and ups_voltage >= minimum_voltage)

    if generator_supplying and ups_live:
        state = "generator_to_ups_candidate"
    elif generator_supplying and not ups_live:
        state = "generator_present_ups_not_live"
    elif grid_available and ups_live:
        state = "grid_to_ups_candidate"
    elif grid_available:
        state = "grid_available_ups_unknown"
    elif generator_available and ups_live:
        state = "generator_available_ups_source_unknown"
    elif generator_available:
        state = "generator_available_not_supplying"
    elif ups_live:
        state = "ups_live_source_unknown"
    else:
        state = "no_ac_source"

    return grid_available, generator_available, generator_supplying, ups_live, state


def _decode_gridboss_telemetry(
    *,
    host: str,
    port: int,
    dongle_serial: str,
    frame: DongleFrame,
    frequency_frame: DongleFrame,
) -> EG4GridBossTelemetry:
    """Decode the GridBOSS input-register ranges used for transfer diagnosis."""
    values = frame.values
    frequencies = frequency_frame.values
    if len(values) < _GRIDBOSS_COUNT:
        raise DongleFrameError(
            f"short GridBOSS register range: expected {_GRIDBOSS_COUNT}, got {len(values)}"
        )
    if len(frequencies) < 11:
        raise DongleFrameError(
            "short GridBOSS frequency range: expected registers 128-130"
        )
    # During a real source/transfer event the GridBOSS input block can return
    # a different complete register image. Do not misclassify a partial image
    # as a valid transfer sample when all terminal voltages are unavailable.
    voltage_values = tuple(
        _gridboss_register_value(values, register)
        for register in range(1, 10)
    )
    if all(value is None for value in voltage_values):
        raise DongleFrameError("GridBOSS input voltage block unavailable")

    input_voltage_raw = tuple(
        _gridboss_register_value(values, register) for register in range(1, 10)
    )
    input_registers_valid = all(
        raw is not None and raw <= 5_000 for raw in input_voltage_raw
    )

    grid_l1_power = _gridboss_power(values, 26) if input_registers_valid else None
    grid_l2_power = _gridboss_power(values, 27) if input_registers_valid else None
    load_l1_power = _gridboss_power(values, 28) if input_registers_valid else None
    load_l2_power = _gridboss_power(values, 29) if input_registers_valid else None
    generator_l1_power = (
        _gridboss_power(values, 30) if input_registers_valid else None
    )
    generator_l2_power = (
        _gridboss_power(values, 31) if input_registers_valid else None
    )
    ups_l1_power = _gridboss_power(values, 32) if input_registers_valid else None
    ups_l2_power = _gridboss_power(values, 33) if input_registers_valid else None

    def summed(first: float | None, second: float | None) -> float | None:
        if first is None or second is None:
            return None
        return first + second

    grid_voltage = _gridboss_voltage(values, 1) if input_registers_valid else None
    ups_voltage = _gridboss_voltage(values, 2) if input_registers_valid else None
    generator_voltage = (
        _gridboss_voltage(values, 3) if input_registers_valid else None
    )
    phase_lock_frequency = _gridboss_frequency(
        frequencies, 128, _GRIDBOSS_FREQUENCY_REGISTER
    )
    grid_frequency = _gridboss_frequency(
        frequencies, 129, _GRIDBOSS_FREQUENCY_REGISTER
    )
    generator_frequency = _gridboss_frequency(
        frequencies, 130, _GRIDBOSS_FREQUENCY_REGISTER
    )
    (
        grid_available,
        generator_available,
        generator_supplying,
        ups_live,
        transfer_state,
    ) = classify_gridboss_transfer(
        grid_voltage=grid_voltage,
        grid_frequency=grid_frequency,
        generator_voltage=generator_voltage,
        generator_frequency=generator_frequency,
        generator_power=summed(generator_l1_power, generator_l2_power),
        ups_voltage=ups_voltage,
    )
    if not input_registers_valid:
        transfer_state = "input_register_block_invalid"

    return EG4GridBossTelemetry(
        host=host,
        port=port,
        dongle_serial=dongle_serial,
        inverter_serial=frame.inverter_serial,
        grid_voltage=grid_voltage,
        ups_voltage=ups_voltage,
        generator_voltage=generator_voltage,
        grid_l1_voltage=(
            _gridboss_voltage(values, 4) if input_registers_valid else None
        ),
        grid_l2_voltage=(
            _gridboss_voltage(values, 5) if input_registers_valid else None
        ),
        ups_l1_voltage=(
            _gridboss_voltage(values, 6) if input_registers_valid else None
        ),
        ups_l2_voltage=(
            _gridboss_voltage(values, 7) if input_registers_valid else None
        ),
        generator_l1_voltage=(
            _gridboss_voltage(values, 8) if input_registers_valid else None
        ),
        generator_l2_voltage=(
            _gridboss_voltage(values, 9) if input_registers_valid else None
        ),
        grid_l1_current=(
            _gridboss_current(values, 10) if input_registers_valid else None
        ),
        grid_l2_current=(
            _gridboss_current(values, 11) if input_registers_valid else None
        ),
        ups_l1_current=(
            _gridboss_current(values, 16) if input_registers_valid else None
        ),
        ups_l2_current=(
            _gridboss_current(values, 17) if input_registers_valid else None
        ),
        generator_l1_current=(
            _gridboss_current(values, 14) if input_registers_valid else None
        ),
        generator_l2_current=(
            _gridboss_current(values, 15) if input_registers_valid else None
        ),
        grid_power=summed(grid_l1_power, grid_l2_power),
        load_power=summed(load_l1_power, load_l2_power),
        ups_power=summed(ups_l1_power, ups_l2_power),
        generator_power=summed(generator_l1_power, generator_l2_power),
        grid_l1_power=grid_l1_power,
        grid_l2_power=grid_l2_power,
        load_l1_power=load_l1_power,
        load_l2_power=load_l2_power,
        ups_l1_power=ups_l1_power,
        ups_l2_power=ups_l2_power,
        generator_l1_power=generator_l1_power,
        generator_l2_power=generator_l2_power,
        phase_lock_frequency=phase_lock_frequency,
        grid_frequency=grid_frequency,
        generator_frequency=generator_frequency,
        grid_available=grid_available,
        generator_available=generator_available,
        generator_supplying=generator_supplying,
        ups_live=ups_live,
        transfer_state=transfer_state,
        input_registers=tuple(values),
        frequency_registers=tuple(frequencies),
        input_registers_valid=input_registers_valid,
    )


def _decode_core_telemetry(
    *,
    host: str,
    port: int,
    frame: DongleFrame,
    dongle_serial: str,
) -> EG4LocalTelemetry:
    """Decode the fast-changing inverter/core register range."""
    values = frame.values
    if len(values) < _CORE_COUNT:
        raise DongleFrameError(
            f"short core register range: expected {_CORE_COUNT}, got {len(values)}"
        )
    status_code = values[0]
    if status_code not in _VALID_STATUS_CODES:
        raise DongleFrameError(f"invalid inverter status register: {status_code}")
    grid_frequency = values[15] / 100.0
    eps_frequency = values[23] / 100.0
    if values[12] > 800 and not 45.0 <= grid_frequency <= 70.0:
        raise DongleFrameError(
            f"implausible grid frequency for live voltage: {grid_frequency}"
        )
    if values[20] > 800 and not 45.0 <= eps_frequency <= 70.0:
        raise DongleFrameError(
            f"implausible EPS frequency for live voltage: {eps_frequency}"
        )
    packed_soc_soh = values[5] if len(values) > 5 else None
    battery_soc = (
        float(packed_soc_soh & 0xFF)
        if packed_soc_soh is not None and (packed_soc_soh & 0xFF) <= 100
        else None
    )
    battery_soh = (
        float((packed_soc_soh >> 8) & 0xFF)
        if packed_soc_soh is not None and ((packed_soc_soh >> 8) & 0xFF) <= 100
        else None
    )

    def scaled(index: int, divisor: float, upper: float) -> float | None:
        return _bounded(
            values[index] / divisor if len(values) > index else None,
            0.0,
            upper,
        )

    return EG4LocalTelemetry(
        host=host,
        port=port,
        dongle_serial=dongle_serial,
        inverter_serial=frame.inverter_serial,
        battery_voltage=_bounded(
            values[4] / 10.0 if len(values) > 4 else None, 40.0, 60.0
        ),
        battery_soc=battery_soc,
        battery_soh=battery_soh,
        charge_power=_bounded(
            float(values[10]) if len(values) > 10 else None, 0.0, 100_000.0
        ),
        discharge_power=_bounded(
            float(values[11]) if len(values) > 11 else None, 0.0, 100_000.0
        ),
        bms_parallel_count=None,
        bms_capacity_ah=None,
        bms_current=None,
        bms_max_cell_voltage=None,
        bms_min_cell_voltage=None,
        bms_max_cell_temperature=None,
        bms_min_cell_temperature=None,
        bms_cycle_count=None,
        bms_available=False,
        status_code=status_code,
        grid_voltage=scaled(12, 10.0, 300.0),
        grid_frequency=scaled(15, 100.0, 100.0),
        inverter_power=(
            _bounded(float(values[16]), 0.0, 100_000.0)
            if len(values) > 16
            else None
        ),
        rectifier_power=(
            _bounded(float(values[17]), 0.0, 100_000.0)
            if len(values) > 17
            else None
        ),
        eps_voltage_r=scaled(20, 10.0, 300.0),
        eps_voltage_s=scaled(21, 10.0, 300.0),
        eps_voltage_t=scaled(22, 10.0, 300.0),
        eps_frequency=scaled(23, 100.0, 100.0),
        eps_power=(
            _bounded(float(values[24]), 0.0, 100_000.0)
            if len(values) > 24
            else None
        ),
        grid_export_power=(
            _bounded(float(values[26]), 0.0, 100_000.0)
            if len(values) > 26
            else None
        ),
        grid_import_power=(
            _bounded(float(values[27]), 0.0, 100_000.0)
            if len(values) > 27
            else None
        ),
        core_registers=tuple(values),
    )


def _decode_bms_data(frame: DongleFrame, dongle_serial: str) -> EG4LocalBMSData:
    """Decode the slower BMS register range."""
    values = frame.values
    parallel_raw = values[16] if len(values) > 16 else None
    capacity_raw = values[17] if len(values) > 17 else None
    current_raw = values[18] if len(values) > 18 else None
    max_cell_raw = values[21] if len(values) > 21 else None
    min_cell_raw = values[22] if len(values) > 22 else None
    max_temp_raw = values[23] if len(values) > 23 else None
    min_temp_raw = values[24] if len(values) > 24 else None
    cycle_raw = values[26] if len(values) > 26 else None
    available = bool(
        parallel_raw is not None
        and 1 <= parallel_raw <= 64
        and max_cell_raw is not None
        and min_cell_raw is not None
        and 2000 <= min_cell_raw <= max_cell_raw <= 4500
    )
    return EG4LocalBMSData(
        dongle_serial=dongle_serial,
        inverter_serial=frame.inverter_serial,
        parallel_count=(
            int(parallel_raw) if parallel_raw is not None and 1 <= parallel_raw <= 64 else None
        ),
        capacity_ah=_bounded(
            float(capacity_raw) if capacity_raw is not None else None,
            0.0,
            10_000.0,
        ),
        current=_bounded(
            float(struct.unpack("<h", struct.pack("<H", current_raw))[0]) / 100.0
            if current_raw is not None
            else None,
            -1_000.0,
            1_000.0,
        ),
        max_cell_voltage=_bounded(
            max_cell_raw / 1000.0 if max_cell_raw is not None else None,
            2.0,
            4.5,
        ),
        min_cell_voltage=_bounded(
            min_cell_raw / 1000.0 if min_cell_raw is not None else None,
            2.0,
            4.5,
        ),
        max_cell_temperature=_bounded(
            max_temp_raw / 10.0 if max_temp_raw is not None else None,
            -50.0,
            100.0,
        ),
        min_cell_temperature=_bounded(
            min_temp_raw / 10.0 if min_temp_raw is not None else None,
            -50.0,
            100.0,
        ),
        cycle_count=_bounded(
            float(cycle_raw) if cycle_raw is not None else None,
            0.0,
            1_000_000.0,
        ),
        available=available,
        registers=tuple(values),
    )


def merge_local_telemetry(
    core: EG4LocalTelemetry,
    bms: EG4LocalBMSData | None,
) -> EG4LocalTelemetry:
    """Combine a fast core sample with the latest BMS sample."""
    if bms is None:
        return core
    return replace(
        core,
        dongle_serial=bms.dongle_serial,
        inverter_serial=bms.inverter_serial,
        bms_parallel_count=bms.parallel_count,
        bms_capacity_ah=bms.capacity_ah,
        bms_current=bms.current,
        bms_max_cell_voltage=bms.max_cell_voltage,
        bms_min_cell_voltage=bms.min_cell_voltage,
        bms_max_cell_temperature=bms.max_cell_temperature,
        bms_min_cell_temperature=bms.min_cell_temperature,
        bms_cycle_count=bms.cycle_count,
        bms_available=bms.available,
        bms_registers=bms.registers,
    )


class EG4LocalDongle:
    """Short-lived, read-only client for one EG4 Wi-Fi dongle.

    A new TCP connection is used for each register range. An unsolicited cached
    frame may arrive after connect. Clients for the configured host/port share
    request serialization; a hard hardware single-client limit is not established.
    """

    def __init__(
        self,
        *,
        host: str,
        inverter_serial: str | None = None,
        port: int = 8000,
        timeout: float = 5.0,
        evidence_sink: Callable[[dict[str, Any]], Awaitable[object]] | None = None,
        role: str = "unknown",
        session_id: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.inverter_serial = inverter_serial
        self.timeout = timeout
        self.evidence_sink = evidence_sink
        self.role = role
        self.session_id = session_id or str(uuid4())
        route = (host, port)
        request_lock = _ROUTE_REQUEST_LOCKS.get(route)
        if request_lock is None:
            request_lock = asyncio.Lock()
            _ROUTE_REQUEST_LOCKS[route] = request_lock
        self._request_lock = request_lock
        self._capture_context: ContextVar[dict[str, Any] | None] = ContextVar(
            f"eg4_capture_{id(self)}", default=None
        )

    @contextlib.contextmanager
    def capture(self) -> Iterator[dict[str, Any]]:
        """Group public reads, including wait_for child tasks, into one acquisition."""
        acquisition: dict[str, Any] = {
            "acquisition_id": str(uuid4()),
            "transaction_ids": [],
            "successful_transaction_ids": [],
            "failed_transaction_ids": [],
        }
        token = self._capture_context.set(acquisition)
        try:
            yield acquisition
        finally:
            self._capture_context.reset(token)

    @contextlib.contextmanager
    def _acquisition(self) -> Iterator[dict[str, Any]]:
        """Reuse the caller's task-local scope or create one for a standalone read."""
        existing = self._capture_context.get()
        if existing is not None:
            yield existing
        else:
            with self.capture() as acquisition:
                yield acquisition

    async def _emit(self, record: dict[str, Any]) -> None:
        """Queue evidence without replacing a device result with recorder failure."""
        if self.evidence_sink is not None:
            # Recorder failures must not replace device errors or measurements.
            # The queue-based writer owns loss/error accounting.
            with contextlib.suppress(Exception):
                await self.evidence_sink(record)

    async def _interpret(
        self,
        transactions: tuple[dict[str, Any], ...],
        decode: Callable[[], _Decoded],
    ) -> _Decoded:
        """Record decoding outcomes separately from the immutable raw transactions."""
        error = None
        try:
            result = decode()
        except Exception as err:
            error = {"type": type(err).__name__, "stage": "decode", "message": str(err)}
            raise
        finally:
            for transaction in transactions:
                interpretation = {
                    "schema": LOCAL_DONGLE_TRACE_SCHEMA,
                    "kind": "interpretation",
                    "session_id": transaction["session_id"],
                    "role": transaction["role"],
                    "acquisition_id": transaction["acquisition_id"],
                    "transaction_id": transaction["transaction_id"],
                    "started_at": transaction["started_at"],
                    "completed_at": transaction["completed_at"],
                    "duration_ms": transaction["duration_ms"],
                    "semantic": "accepted" if error is None else "rejected",
                    "error": error,
                }
                if error is None:
                    await self._emit(interpretation)
                else:
                    with contextlib.suppress(BaseException):
                        await self._emit(interpretation)
        return result

    async def _transaction(
        self,
        register: int | None,
        count: int | None,
        *,
        function: int | None = None,
        timeout: float | None = None,
    ) -> tuple[DongleFrame, str, dict[str, Any]]:
        """Serialize and journal one bounded identity/read attempt before returning."""
        with self._acquisition() as acquisition:
            loop = asyncio.get_running_loop()
            started = loop.time()
            deadline = started + (self.timeout if timeout is None else timeout)
            transaction: dict[str, Any] = {
                "schema": LOCAL_DONGLE_TRACE_SCHEMA,
                "kind": "transaction",
                "session_id": self.session_id,
                "role": self.role,
                "acquisition_id": acquisition["acquisition_id"],
                "transaction_id": str(uuid4()),
                "host": self.host,
                "port": self.port,
                "configured_inverter_serial": self.inverter_serial,
                "observed_dongle_serial": None,
                "observed_inverter_serial": None,
                "request": {
                    "function": function, "register": register, "count": count,
                    "bytes_hex": None,
                },
                "started_at": datetime.now(UTC).isoformat(),
                "frames": [],
                "ok": False,
                "error": None,
                "truncated": False,
                "received_bytes": 0,
            }
            acquisition["transaction_ids"].append(transaction["transaction_id"])
            stage = "lock"
            writer: asyncio.StreamWriter | None = None
            locked = False
            failure: BaseException | None = None
            try:
                async with asyncio.timeout_at(deadline):
                    await self._request_lock.acquire()
                    locked = True
                    stage = "connect"
                    reader, writer = await asyncio.open_connection(self.host, self.port)
                    stage = "identity"
                    while True:
                        if loop.time() >= deadline:
                            raise TimeoutError("transaction deadline exhausted during identity")
                        initial = await _read_frame(
                            reader, transaction, dongle_serial=None,
                            inverter_serial=self.inverter_serial,
                            function=None, register=None, count=None,
                        )
                        # Both native A5/A5 history inside translated envelopes
                        # and other envelope functions may precede identity.
                        evidence = transaction["frames"][-1]
                        if evidence["classification"] == "history":
                            if evidence["validation"]["framing"] != "passed":
                                raise DongleFrameError("malformed unsolicited history framing")
                            if evidence["validation"]["crc"] != "passed":
                                raise DongleFrameError("unsolicited history CRC mismatch")
                            continue
                        detected = parse_dongle_frame(
                            initial, expected_inverter_serial=self.inverter_serial
                        )
                        if not detected.dongle_serial or not detected.inverter_serial:
                            raise DongleFrameError("dongle identity frame has empty serials")
                        transaction["frames"][-1]["classification"] = "identity"
                        break
                    dongle_serial = detected.dongle_serial
                    inverter_serial = self.inverter_serial or detected.inverter_serial
                    transaction["observed_dongle_serial"] = dongle_serial
                    transaction["observed_inverter_serial"] = detected.inverter_serial
                    if register is None:
                        self.inverter_serial = detected.inverter_serial
                        transaction["ok"] = True
                        return detected, dongle_serial, transaction
                    assert count is not None and function is not None
                    stage = "request"
                    request = build_read_packet(
                        dongle_serial=dongle_serial, inverter_serial=inverter_serial,
                        register=register, count=count, function=function,
                    )
                    writer.write(request)
                    transaction["request"]["bytes_hex"] = request.hex()
                    await writer.drain()
                    stage = "response"
                    last_error: DongleFrameError | None = None
                    while True:
                        if loop.time() >= deadline:
                            raise TimeoutError("transaction deadline exhausted during response")
                        try:
                            response = await _read_frame(
                                reader, transaction, dongle_serial=dongle_serial,
                                inverter_serial=inverter_serial, function=function,
                                register=register, count=count,
                            )
                        except asyncio.IncompleteReadError as err:
                            if not err.partial and last_error is not None:
                                raise last_error from err
                            raise
                        try:
                            parsed = parse_dongle_frame(
                                response, expected_dongle_serial=dongle_serial,
                                expected_inverter_serial=inverter_serial,
                                expected_register=register, expected_count=count,
                                expected_function=function,
                            )
                        except DongleFrameError as err:
                            last_error = err
                            continue
                        transaction["frames"][-1]["classification"] = "matching"
                        self.inverter_serial = parsed.inverter_serial
                        transaction["ok"] = True
                        return parsed, dongle_serial, transaction
            except BaseException as err:
                failure = err
                transaction["error"] = {
                    "type": type(err).__name__, "stage": stage, "message": str(err),
                }
                raise
            finally:
                outcome = (
                    "successful_transaction_ids" if transaction["ok"]
                    else "failed_transaction_ids"
                )
                acquisition[outcome].append(transaction["transaction_id"])
                transaction["completed_at"] = datetime.now(UTC).isoformat()
                transaction["duration_ms"] = (loop.time() - started) * 1000.0
                try:
                    if writer is not None:
                        writer.close()
                    if failure is None:
                        await self._emit(transaction)
                    else:
                        # In particular, cancellation must retain acquired bytes
                        # while the original CancelledError still propagates.
                        with contextlib.suppress(BaseException):
                            await self._emit(transaction)
                    if writer is not None:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                finally:
                    if locked:
                        self._request_lock.release()

    async def async_discover_identity(self) -> EG4LocalDongleIdentity:
        """Demultiplex unsolicited frames without sending a request."""
        frame, dongle_serial, _ = await self._transaction(None, None)
        return EG4LocalDongleIdentity(
            host=self.host, port=self.port, dongle_serial=dongle_serial,
            inverter_serial=frame.inverter_serial,
        )

    async def _read_registers(
        self,
        register: int,
        count: int,
        *,
        function: int = MODBUS_READ_INPUT,
        timeout: float | None = None,
    ) -> tuple[DongleFrame, str, dict[str, Any]]:
        return await self._transaction(register, count, function=function, timeout=timeout)

    async def async_read_register_block(
        self,
        register: int,
        count: int,
        *,
        function: int = MODBUS_READ_INPUT,
        timeout: float | None = None,
    ) -> DongleFrame:
        """Return one validated FC03/FC04 block after queuing its raw evidence."""
        frame, _, _ = await self._read_registers(
            register, count, function=function, timeout=timeout,
        )
        return frame

    async def async_read_core_snapshot(self) -> EG4LocalTelemetry:
        """Read the fast core range used for second-level tracing."""
        with self._acquisition():
            core, dongle_serial, transaction = await self._read_registers(
                _CORE_REGISTER, _CORE_COUNT
            )
            return await self._interpret(
                (transaction,),
                lambda: _decode_core_telemetry(
                    host=self.host, port=self.port, frame=core,
                    dongle_serial=dongle_serial,
                ),
            )

    async def async_read_bms_snapshot(self) -> EG4LocalBMSData:
        """Read the slower BMS range independently from core telemetry."""
        with self._acquisition():
            bms, dongle_serial, transaction = await self._read_registers(
                _BMS_REGISTER, _BMS_COUNT
            )
            return await self._interpret(
                (transaction,), lambda: _decode_bms_data(bms, dongle_serial)
            )

    async def async_read_snapshot(self) -> EG4LocalTelemetry | EG4GridBossTelemetry:
        """Read core inverter values and the BMS diagnostic range."""
        with self._acquisition():
            core = await self.async_read_core_snapshot()
            bms = await self.async_read_bms_snapshot()
            return merge_local_telemetry(core, bms)


class EG4GridBossDongle(EG4LocalDongle):
    """Read-only client for the GridBOSS input-register telemetry ranges."""

    async def async_read_snapshot(self) -> EG4GridBossTelemetry:
        """Read source/output power ranges and classify the observed transfer."""
        with self._acquisition():
            input_frame, dongle_serial, input_transaction = await self._read_registers(
                _GRIDBOSS_REGISTER, _GRIDBOSS_COUNT
            )
            frequency_frame, _, frequency_transaction = await self._read_registers(
                _GRIDBOSS_FREQUENCY_REGISTER, _GRIDBOSS_FREQUENCY_COUNT
            )
            return await self._interpret(
                (input_transaction, frequency_transaction),
                lambda: _decode_gridboss_telemetry(
                    host=self.host, port=self.port, dongle_serial=dongle_serial,
                    frame=input_frame, frequency_frame=frequency_frame,
                ),
            )


async def async_discover_dongles(
    hosts: list[str] | tuple[str, ...],
    *,
    port: int = 8000,
    timeout: float = 0.5,
    concurrency: int = 24,
) -> tuple[EG4LocalDongleIdentity, ...]:
    """Probe hosts for read-only EG4 identity frames with bounded concurrency."""
    unique_hosts = tuple(sorted({str(host).strip() for host in hosts if str(host).strip()}))
    if not unique_hosts:
        return ()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def probe(host: str) -> EG4LocalDongleIdentity | None:
        async with semaphore:
            dongle = EG4LocalDongle(host=host, port=port, timeout=timeout)
            try:
                return await dongle.async_discover_identity()
            except Exception:
                # Discovery is best-effort: one closed or malformed host must
                # not abort the scan of the remaining local segment.
                return None

    results = await asyncio.gather(*(probe(host) for host in unique_hosts))
    identities = [result for result in results if result is not None]
    return tuple(
        sorted(
            identities,
            key=lambda identity: (identity.inverter_serial, identity.host),
        )
    )


__all__ = [
    "DongleFrame",
    "DongleFrameError",
    "EG4GridBossDongle",
    "EG4GridBossTelemetry",
    "EG4LocalBMSData",
    "EG4LocalDongleIdentity",
    "EG4LocalDongle",
    "async_discover_dongles",
    "EG4LocalTelemetry",
    "build_read_packet",
    "compute_crc16",
    "classify_gridboss_transfer",
    "merge_local_telemetry",
    "parse_dongle_frame",
]
