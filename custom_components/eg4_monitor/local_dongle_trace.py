"""Bounded JSONL tracing for local EG4 dongle telemetry."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from concurrent.futures import Future
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .local_dongle import EG4GridBossTelemetry, EG4LocalTelemetry


TRACE_SCHEMA = "eg4_local_dongle_trace.v1"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_FILES = 8
CONTROLLER_OBSERVATION_SCHEMA = "eg4_controller_observation.v1"
_GRIDBOSS_INPUTS = (
    *range(122, 128), 131, *range(500, 520),
    567, 568, 569, 572, 576, 577, 578, 579, 580, 587, 588,
)
_GRIDBOSS_HOLDINGS = (
    18, 20, 24, 37, 38, 170, 179, 226, 2102, *range(4500, 4510),
)
# FAAB-27xx warnings are 60/61; the 64/65 analog branch is raw context, not a trigger.
# Keep analog command142/143 in raw blocks; trigger on limiter/state144/145.
_FLEXBOSS_INPUTS = (0, 60, 61, 62, 63, 77, 144, 145)
_FLEXBOSS_HOLDINGS = (110, 170, 177, 179, 194, 195, 196, 197, 226)
_TRANSFER_FIELDS = (
    "state", "input_registers_valid", "grid_available",
    "generator_available", "generator_supplying", "ups_live",
    "quality", "confidence",
)
_STATUS_NAMES = {
    0x00: "standby",
    0x04: "pv_on_grid",
    0x08: "pv_charge",
    0x0C: "pv_charge_on_grid",
    0x10: "battery_on_grid",
    0x11: "bypass",
    0x14: "pv_battery_on_grid",
    0x20: "ac_charge",
    0x28: "pv_ac_charge",
    0x40: "battery_off_grid",
    0x80: "pv_off_grid",
    0xC0: "pv_battery_off_grid",
}


def gridboss_to_record(telemetry: EG4GridBossTelemetry) -> dict[str, Any]:
    """Serialize direct GridBOSS measurements and raw input registers."""
    return {
        "host": telemetry.host,
        "port": telemetry.port,
        "dongle_serial": telemetry.dongle_serial,
        "inverter_serial": telemetry.inverter_serial,
        "decoded": {
            "grid_voltage_v": telemetry.grid_voltage,
            "grid_l1_voltage_v": telemetry.grid_l1_voltage,
            "grid_l2_voltage_v": telemetry.grid_l2_voltage,
            "grid_frequency_hz": telemetry.grid_frequency,
            "grid_l1_current_a": telemetry.grid_l1_current,
            "grid_l2_current_a": telemetry.grid_l2_current,
            "grid_power_w": telemetry.grid_power,
            "grid_l1_power_w": telemetry.grid_l1_power,
            "grid_l2_power_w": telemetry.grid_l2_power,
            "generator_voltage_v": telemetry.generator_voltage,
            "generator_l1_voltage_v": telemetry.generator_l1_voltage,
            "generator_l2_voltage_v": telemetry.generator_l2_voltage,
            "generator_frequency_hz": telemetry.generator_frequency,
            "generator_l1_current_a": telemetry.generator_l1_current,
            "generator_l2_current_a": telemetry.generator_l2_current,
            "generator_power_w": telemetry.generator_power,
            "generator_l1_power_w": telemetry.generator_l1_power,
            "generator_l2_power_w": telemetry.generator_l2_power,
            "ups_voltage_v": telemetry.ups_voltage,
            "ups_l1_voltage_v": telemetry.ups_l1_voltage,
            "ups_l2_voltage_v": telemetry.ups_l2_voltage,
            "ups_l1_current_a": telemetry.ups_l1_current,
            "ups_l2_current_a": telemetry.ups_l2_current,
            "ups_power_w": telemetry.ups_power,
            "ups_l1_power_w": telemetry.ups_l1_power,
            "ups_l2_power_w": telemetry.ups_l2_power,
            "load_power_w": telemetry.load_power,
            "phase_lock_frequency_hz": telemetry.phase_lock_frequency,
        },
        "transfer": {
            "grid_available": telemetry.grid_available,
            "generator_available": telemetry.generator_available,
            "generator_supplying": telemetry.generator_supplying,
            "ups_live": telemetry.ups_live,
            "state": telemetry.transfer_state,
            "input_registers_valid": telemetry.input_registers_valid,
        },
        "raw": {
            "input_registers_start_0": telemetry.input_registers,
            "frequency_registers_start_120": telemetry.frequency_registers,
        },
    }


def telemetry_to_record(telemetry: EG4LocalTelemetry) -> dict[str, Any]:
    """Serialize decoded and raw local telemetry without credentials."""
    return {
        "host": telemetry.host,
        "port": telemetry.port,
        "dongle_serial": telemetry.dongle_serial,
        "inverter_serial": telemetry.inverter_serial,
        "core": {
            "status_code": telemetry.status_code,
            "status_name": _STATUS_NAMES.get(telemetry.status_code, "unknown"),
            "battery_voltage_v": telemetry.battery_voltage,
            "soc_percent": telemetry.battery_soc,
            "soh_percent": telemetry.battery_soh,
            "charge_power_w": telemetry.charge_power,
            "discharge_power_w": telemetry.discharge_power,
            "grid_voltage_v": telemetry.grid_voltage,
            "grid_frequency_hz": telemetry.grid_frequency,
            "inverter_power_w": telemetry.inverter_power,
            "rectifier_power_w": telemetry.rectifier_power,
            "eps_voltage_r_v": telemetry.eps_voltage_r,
            "eps_voltage_s_v": telemetry.eps_voltage_s,
            "eps_voltage_t_v": telemetry.eps_voltage_t,
            "eps_frequency_hz": telemetry.eps_frequency,
            "eps_power_w": telemetry.eps_power,
            "grid_export_power_w": telemetry.grid_export_power,
            "grid_import_power_w": telemetry.grid_import_power,
            "registers": telemetry.core_registers,
        },
        "bms": {
            "available": telemetry.bms_available,
            "parallel_count": telemetry.bms_parallel_count,
            "capacity_ah": telemetry.bms_capacity_ah,
            "current_a": telemetry.bms_current,
            "max_cell_voltage_v": telemetry.bms_max_cell_voltage,
            "min_cell_voltage_v": telemetry.bms_min_cell_voltage,
            "max_cell_temperature_c": telemetry.bms_max_cell_temperature,
            "min_cell_temperature_c": telemetry.bms_min_cell_temperature,
            "cycle_count": telemetry.bms_cycle_count,
            "registers": telemetry.bms_registers,
        },
    }


class ControllerObservationTracker:
    """Keep first and changed raw controller observations, separately per role.

    Decision bytes are last-written firmware diagnostics, never contact position
    or an assertion that the recorded reason remains active. No debounce applies.
    Missing input is represented by None, not by a synthetic zero.
    """

    def __init__(self) -> None:
        self._previous: dict[str, tuple[tuple[str, Any], ...]] = {}

    def observe(self, record: dict) -> dict | None:
        """Return durable context on change, excluding unrelated HA states."""
        role = record["role"]
        gridboss = role.startswith("gridboss")
        inputs = _GRIDBOSS_INPUTS if gridboss else _FLEXBOSS_INPUTS
        holdings = _GRIDBOSS_HOLDINGS if gridboss else _FLEXBOSS_HOLDINGS
        registers: dict[tuple[int, int], int] = {}
        telemetry = record.get("telemetry") or {}
        primary_ok = record.get("ok") is True
        if primary_ok:
            if gridboss:
                raw = telemetry.get("raw") or {}
                primary_blocks = (
                    (0, raw.get("input_registers_start_0")),
                    (120, raw.get("frequency_registers_start_120")),
                )
            else:
                primary_blocks = (
                    (0, (telemetry.get("core") or {}).get("registers")),
                )
            for start, values in primary_blocks:
                if values is not None:
                    registers.update(
                        ((4, start + offset), value)
                        for offset, value in enumerate(values)
                    )

        expected = (
            ((4, 500, 100), (3, 4500, 10)) if gridboss
            else ((4, 40, 40), (3, 110, 125), (4, 120, 26))
        )
        blocks = {f"{fn}:{start}:{count}": None for fn, start, count in expected}
        for block in record.get("diagnostics") or ():
            function = block["function"]
            start = block["register"]
            count = block["count"]
            values = block.get("values")
            ok = block.get("ok") is True and values is not None
            blocks[f"{function}:{start}:{count}"] = ok
            if ok:
                registers.update(
                    ((function, start + offset), value)
                    for offset, value in enumerate(values)
                )

        controller = {
            "input": {str(reg): registers.get((4, reg)) for reg in inputs},
            "holding": {str(reg): registers.get((3, reg)) for reg in holdings},
        }
        transfer = (telemetry.get("transfer") or {}) if primary_ok else {}
        fields: dict[str, Any] = {
            "availability.primary": primary_ok,
            **{f"availability.diagnostic.{key}": value for key, value in blocks.items()},
            **{
                f"{kind}.{reg}": value
                for kind, values in controller.items()
                for reg, value in values.items()
            },
            **{f"transfer.{key}": transfer.get(key) for key in _TRANSFER_FIELDS},
        }
        signature = tuple(sorted(fields.items()))
        previous_signature = self._previous.get(role)
        if previous_signature == signature:
            return None
        self._previous[role] = signature
        previous = dict(previous_signature) if previous_signature is not None else None
        changed = (
            sorted(
                key for key in fields.keys() | previous.keys()
                if fields.get(key) != previous.get(key)
            )
            if previous is not None else []
        )
        observation = "baseline"
        if previous is not None:
            lost = any(
                (
                    previous.get(key) is True and fields.get(key) is not True
                    if key.startswith("availability.")
                    else previous.get(key) is not None and fields.get(key) is None
                )
                for key in changed
            )
            resumed = any(
                (
                    previous.get(key) is not True and fields.get(key) is True
                    if key.startswith("availability.")
                    else previous.get(key) is None and fields.get(key) is not None
                )
                for key in changed
            )
            if lost and resumed:
                observation = "availability_changed"
            elif lost:
                observation = "availability_lost"
            elif resumed:
                observation = "availability_resumed"
            elif any(key.startswith("availability.") for key in changed):
                observation = "availability_changed"
            else:
                observation = "controller_changed"
        if gridboss:
            controller["decisions"] = {
                str(reg): {
                    "state_byte": word & 0xFF if word is not None else None,
                    "reason_byte": word >> 8 if word is not None else None,
                }
                for reg in range(514, 520)
                for word in (registers.get((4, reg)),)
            }
        # Snapshot only emitted records; retained comparison state is scalar tuples.
        context = deepcopy({
            key: value for key, value in record.items()
            if key not in ("ha_states", "transfer_transition")
        })
        return {
            **context,
            "schema": CONTROLLER_OBSERVATION_SCHEMA,
            "observation": observation,
            "changed_fields": changed,
            "availability": {"primary": primary_ok, "diagnostics": blocks},
            "controller": controller,
            "observed_transfer": {
                key: transfer.get(key) for key in _TRANSFER_FIELDS
            },
        }


class LocalDongleTraceWriter:
    """Append bounded, rotation-safe JSONL records from an HA task."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        self.root = Path(root)
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.max_files = max(2, int(max_files))
        self._queue: queue.Queue[
            tuple[bytes, Future[Path | None] | None] | None
        ] = queue.Queue(maxsize=1024)
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="eg4-dongle-trace",
            daemon=True,
        )
        self._thread.start()

    @property
    def active_path(self) -> Path:
        """Return the current trace path."""
        return self.root / "local-dongle.jsonl"

    async def async_write(
        self, record: dict[str, Any], *, wait: bool = True
    ) -> Path | None:
        """Queue one record without blocking the HA event loop."""
        enriched = {
            "schema": TRACE_SCHEMA,
            "written_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            **record,
        }
        try:
            line = (
                json.dumps(enriched, separators=(",", ":"), allow_nan=False) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as err:
            self.last_error = f"{type(err).__name__}: {err}"
            return None
        if self._closed:
            self.last_error = "trace writer is closed"
            return None
        result: Future[Path | None] | None = Future() if wait else None
        try:
            self._queue.put_nowait((line, result))
        except queue.Full:
            self.last_error = "trace writer queue is full"
            return None
        if result is None:
            return self.active_path
        return await asyncio.wrap_future(result)

    async def async_close(self) -> None:
        """Drain and flush every accepted record without blocking the HA loop."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(asyncio.to_thread(self._close_sync))
        await asyncio.shield(self._close_task)

    def _close_sync(self) -> None:
        """Queue the stop marker after pending writes, even if the queue is full."""
        self._queue.put(None)
        self._thread.join()

    def _writer_loop(self) -> None:
        """Drain queued writes on a daemon thread."""
        while True:
            item = self._queue.get()
            if item is None:
                return
            line, result = item
            if result is not None and not result.set_running_or_notify_cancel():
                # Cancellation of the awaiting task must not discard its record
                # or kill the worker when it later tries to resolve the future.
                result = None
            try:
                path = self._write_sync(line)
            except OSError as err:
                self.last_error = f"{type(err).__name__}: {err}"
                if result is not None:
                    result.set_result(None)
            else:
                self.last_error = None
                if result is not None:
                    result.set_result(path)

    def _write_sync(self, line: bytes) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        active = self.active_path
        if active.exists() and active.stat().st_size + len(line) > self.max_bytes:
            rotated = self.root / (
                "local-dongle-"
                f"{time.time_ns()}"
                ".jsonl"
            )
            active.replace(rotated)
        with active.open("ab") as stream:
            stream.write(line)
            stream.flush()
        self._prune_sync()
        return active

    def _prune_sync(self) -> None:
        files = sorted(
            self.root.glob("local-dongle*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in files[self.max_files :]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
