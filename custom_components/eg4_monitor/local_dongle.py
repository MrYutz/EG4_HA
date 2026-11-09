"""Read-only local transport for EG4/LuxPower Wi-Fi dongles."""

from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass, replace
from datetime import UTC, datetime

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


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    """Read one length-delimited A1/1A frame from a TCP stream."""
    header = await reader.readexactly(6)
    if header[:2] != PACKET_PREFIX:
        raise DongleFrameError("stream did not start with an A1/1A frame")
    advertised_length = struct.unpack("<H", header[4:6])[0]
    packet_size = 6 + advertised_length
    if packet_size > _MAX_PACKET_SIZE:
        raise DongleFrameError("dongle advertised an oversized frame")
    return header + await reader.readexactly(advertised_length)


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
    frame may arrive after connect. Request serialization is integration-local;
    a hard hardware single-client limit has not been established.
    """

    # FAAB-27xx handlers 08042f80/08042c58 copy fully initialized ranges from
    # serializers 0805c61c/0805e73c. These are not GridBOSS holding indices.
    _DIAGNOSTIC_RANGES = (
        (MODBUS_READ_INPUT, 40, 40),
        (MODBUS_READ_HOLDING, 110, 125),
        (MODBUS_READ_INPUT, 120, 26),
    )

    def __init__(
        self,
        *,
        host: str,
        inverter_serial: str | None = None,
        port: int = 8000,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        # The unsolicited first frame carries the inverter serial. Keeping
        # this optional lets host-only configuration discover it while still
        # allowing callers to pin a serial for route validation.
        self.inverter_serial = inverter_serial
        self.timeout = timeout

    async def async_discover_identity(self) -> EG4LocalDongleIdentity:
        """Read the unsolicited identity frame without sending a request."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        try:
            frame = await asyncio.wait_for(_read_frame(reader), timeout=self.timeout)
            parsed = parse_dongle_frame(frame)
            if not parsed.dongle_serial or not parsed.inverter_serial:
                raise DongleFrameError("dongle identity frame has empty serials")
            if (
                self.inverter_serial is not None
                and parsed.inverter_serial != self.inverter_serial
            ):
                raise DongleFrameError(
                    "discovered inverter serial does not match the configured serial"
                )
            self.inverter_serial = parsed.inverter_serial
            return EG4LocalDongleIdentity(
                host=self.host,
                port=self.port,
                dongle_serial=parsed.dongle_serial,
                inverter_serial=parsed.inverter_serial,
            )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)

    async def _read_registers(
        self,
        register: int,
        count: int,
        *,
        function: int = MODBUS_READ_INPUT,
        timeout: float | None = None,
    ) -> tuple[DongleFrame, str]:
        """Read one range and return its validated frame plus detected serials."""
        stage_timeout = self.timeout if timeout is None else timeout
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=stage_timeout,
        )
        try:
            initial = await asyncio.wait_for(_read_frame(reader), timeout=stage_timeout)
            try:
                detected = parse_dongle_frame(initial)
                dongle_serial = detected.dongle_serial
                detected_inverter_serial = detected.inverter_serial
            except DongleFrameError:
                dongle_serial = ""
                detected_inverter_serial = ""
            if not dongle_serial or not detected_inverter_serial:
                raise DongleFrameError("dongle did not provide identifiable serials")

            inverter_serial = self.inverter_serial or detected_inverter_serial
            writer.write(
                build_read_packet(
                    dongle_serial=dongle_serial,
                    inverter_serial=inverter_serial,
                    register=register,
                    count=count,
                    function=function,
                )
            )
            await asyncio.wait_for(writer.drain(), timeout=stage_timeout)
            deadline = asyncio.get_running_loop().time() + stage_timeout
            last_error: DongleFrameError | None = None
            # The dongle can interleave unsolicited cached frames with the
            # response to an active request. Keep reading until the requested
            # range arrives instead of treating a valid-but-unrelated frame as
            # a failed sample.
            for _ in range(8):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                response = await asyncio.wait_for(
                    _read_frame(reader), timeout=remaining
                )
                try:
                    parsed = parse_dongle_frame(
                        response,
                        expected_dongle_serial=dongle_serial,
                        expected_inverter_serial=inverter_serial,
                        expected_register=register,
                        expected_count=count,
                        expected_function=function,
                    )
                except DongleFrameError as err:
                    last_error = err
                    continue
                # Preserve the auto-detected route for the second range. A
                # configured serial was validated above and is retained unchanged.
                self.inverter_serial = parsed.inverter_serial
                return parsed, dongle_serial
            if last_error is not None:
                raise last_error
            raise DongleFrameError(
                f"no matching register response for {register} after request"
            )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    writer.wait_closed(), timeout=min(stage_timeout, 0.5)
                )

    async def async_read_core_snapshot(self) -> EG4LocalTelemetry:
        """Read the fast core range used for second-level tracing."""
        core, dongle_serial = await self._read_registers(_CORE_REGISTER, _CORE_COUNT)
        return _decode_core_telemetry(
            host=self.host,
            port=self.port,
            frame=core,
            dongle_serial=dongle_serial,
        )

    async def async_read_bms_snapshot(self) -> EG4LocalBMSData:
        """Read the slower BMS range independently from core telemetry."""
        bms, dongle_serial = await self._read_registers(_BMS_REGISTER, _BMS_COUNT)
        return _decode_bms_data(bms, dongle_serial)

    async def async_read_snapshot(self) -> EG4LocalTelemetry | EG4GridBossTelemetry:
        """Read core inverter values and the BMS diagnostic range."""
        core = await self.async_read_core_snapshot()
        bms = await self.async_read_bms_snapshot()
        return merge_local_telemetry(core, bms)

    async def async_read_diagnostics(self) -> list[dict]:
        """Capture independent, read-only blocks without failing primary telemetry."""
        blocks: list[dict] = []
        loop = asyncio.get_running_loop()
        for function, register, count in self._DIAGNOSTIC_RANGES:
            started_at = datetime.now(UTC).isoformat()
            started = loop.time()
            block = {
                "function": function,
                "register": register,
                "count": count,
                "started_at": started_at,
                "ok": False,
                "values": None,
            }
            try:
                frame, _ = await self._read_registers(
                    register,
                    count,
                    function=function,
                    timeout=min(self.timeout, 2.0),
                )
            except (
                OSError,
                asyncio.TimeoutError,
                asyncio.IncompleteReadError,
                DongleFrameError,
            ) as err:
                block["error"] = f"{type(err).__name__}: {err}"
            else:
                block["ok"] = True
                block["values"] = frame.values
            block["completed_at"] = datetime.now(UTC).isoformat()
            block["duration_ms"] = (loop.time() - started) * 1000.0
            blocks.append(block)
        return blocks


class EG4GridBossDongle(EG4LocalDongle):
    """Read-only client for the GridBOSS input-register telemetry ranges."""

    # Local 0800e9b0 copies the 20-byte GPIO serializer at 2*(start-4500).
    _DIAGNOSTIC_RANGES = (
        (MODBUS_READ_INPUT, 500, 100),
        (MODBUS_READ_HOLDING, 4500, 10),
    )

    async def async_read_snapshot(self) -> EG4GridBossTelemetry:
        """Read source/output power ranges and classify the observed transfer."""
        input_frame, dongle_serial = await self._read_registers(
            _GRIDBOSS_REGISTER, _GRIDBOSS_COUNT
        )
        frequency_frame, _ = await self._read_registers(
            _GRIDBOSS_FREQUENCY_REGISTER, _GRIDBOSS_FREQUENCY_COUNT
        )
        return _decode_gridboss_telemetry(
            host=self.host,
            port=self.port,
            dongle_serial=dongle_serial,
            frame=input_frame,
            frequency_frame=frequency_frame,
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
