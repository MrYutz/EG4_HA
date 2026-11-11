"""Bounded JSONL tracing for local EG4 dongle telemetry."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractContextManager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .const import LOCAL_DONGLE_TRACE_SCHEMA
from .local_dongle import EG4GridBossTelemetry, EG4LocalTelemetry
from .register_catalog import GRIDBOSS_CURRENT_INTERPRETATION

DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_FILES = 8
CONTROLLER_OBSERVATION_SCHEMA = "eg4_controller_observation.v1"
_PERSISTENCE_PREFIX_BYTES = len(
    b'{"persistence_started_at":"2000-01-01T00:00:00.000+00:00",'
) - 1
_GRIDBOSS_INPUTS = (
    *range(122, 128), 131, *range(500, 520),
    567, 568, 569, 572, 576, 577, 578, 579, 580, 587, 588,
)
_GRIDBOSS_HOLDINGS = (
    18, 20, 24, 37, 38, 170, 179, 226, 2102, *range(4500, 4510),
)
# FAAB-27xx warnings are 60/61; the 64/65 analog branch is raw context, not a trigger.
# Keep analog command142/143 in raw blocks; trigger on limiter/state144/145.
_FLEXBOSS_INPUTS = (0, 6, 60, 61, 62, 63, 77, 144, 145)
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
            "load_l1_power_w": telemetry.load_l1_power,
            "load_l2_power_w": telemetry.load_l2_power,
            "phase_lock_frequency_hz": telemetry.phase_lock_frequency,
        },
        "interpretation": {
            "current_a": GRIDBOSS_CURRENT_INTERPRETATION,
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
            "status_name": (
                _STATUS_NAMES.get(telemetry.status_code, "unknown")
                if telemetry.status_code is not None else "unknown"
            ),
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


@dataclass(frozen=True)
class PersistenceResult:
    """Report an actual append/flush attempt, not queue acceptance."""

    path: Path | None
    error: str | None
    completed_at: str


@dataclass(frozen=True)
class _PendingObservation:
    """Keep scalar comparison state until an explicit persistence outcome."""

    role: str
    signature: tuple[tuple[str, Any], ...]
    generation: int
    recovery: bool


class ControllerObservationTracker:
    """Keep first and changed raw controller observations, separately per role.

    Decision bytes are last-written firmware diagnostics, never contact position
    or an assertion that the recorded reason remains active. No debounce applies.
    Missing input is represented by None, not by a synthetic zero.
    """

    def __init__(self) -> None:
        self._observed: dict[str, tuple[tuple[str, Any], ...]] = {}
        self._persisted: dict[str, tuple[int, tuple[tuple[str, Any], ...]]] = {}
        self._pending: dict[int, _PendingObservation] = {}
        self._broken: dict[str, int] = {}
        self._loss_generation = 0
        self._sequence = 0

    @property
    def health(self) -> dict[str, Any]:
        """Return observation continuity without treating pending work as saved."""
        return {
            "ContinuityBroken": bool(self._broken),
            "PendingObservations": len(self._pending),
            "ObservedRoles": sorted(self._observed),
            "PersistedRoles": sorted(self._persisted),
        }

    def mark_loss(self, role: str | None = None) -> None:
        """Break continuity until a new-generation baseline actually persists."""
        self._loss_generation += 1
        for affected in self._observed if role is None else (role,):
            self._broken[affected] = self._loss_generation

    def mark_pending(self, observation: dict[str, Any]) -> int:
        """Reserve a bounded receipt token immediately before queue submission."""
        self._sequence += 1
        role = observation["role"]
        self._pending[self._sequence] = _PendingObservation(
            role, self._observed[role], self._broken.get(role, 0),
            observation["observation"] == "recovery_baseline",
        )
        return self._sequence

    def reject(self, token: int) -> None:
        """Record explicit queue loss; rejection is not a write acknowledgement."""
        pending = self._pending.pop(token)
        self.mark_loss(pending.role)

    def acknowledge(self, token: int, result: PersistenceResult) -> None:
        """Advance saved comparison state only from an actual write receipt."""
        pending = self._pending.pop(token)
        if result.path is None or result.error is not None:
            self.mark_loss(pending.role)
            return
        previous = self._persisted.get(pending.role)
        if previous is None or token > previous[0]:
            self._persisted[pending.role] = (token, pending.signature)
        if pending.recovery and self._broken.get(pending.role) == pending.generation:
            del self._broken[pending.role]

    def observe(self, record: dict) -> dict | None:
        """Return baseline/change context; observation alone is not persistence."""
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
        # Structural evidence is usable even when semantic telemetry was rejected.
        # Preserve the complete source blocks/times below; never mark it primary-ok.
        for block in record.get("primary_blocks") or ():
            values = block.get("values")
            if block.get("ok") is True and values is not None:
                registers.update(
                    ((block["function"], block["register"] + offset), value)
                    for offset, value in enumerate(values)
                )

        expected = (
            ((4, 500, 100), (3, 4500, 10)) if gridboss
            else ((4, 40, 40), (3, 110, 125), (4, 120, 26))
        )
        blocks: dict[str, bool | None] = {
            f"{fn}:{start}:{count}": None for fn, start, count in expected
        }
        for block in record.get("diagnostics") or ():
            function = block["function"]
            start = block["register"]
            count = block["count"]
            values = block.get("values")
            ok = block.get("ok") is True and values is not None
            blocks[f"{function}:{start}:{count}"] = ok
            if ok and values is not None:
                registers.update(
                    ((function, start + offset), value)
                    for offset, value in enumerate(values)
                )

        controller: dict[str, Any] = {
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
        if not gridboss:
            word = registers.get((4, 6))
            fields.pop("input.6")
            fields["input.6.bits_0_1"] = word & 3 if word is not None else None
        signature = tuple(sorted(fields.items()))
        previous_signature = self._observed.get(role)
        self._observed[role] = signature
        recovery_pending = any(
            pending.role == role and pending.recovery
            and pending.generation == self._broken.get(role)
            for pending in self._pending.values()
        )
        recovery = role in self._broken and not recovery_pending
        if previous_signature == signature and not recovery:
            return None
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
        if recovery:
            observation = "recovery_baseline"
        if not gridboss:
            controller["input6_semantics"] = {
                "trigger_mask": 3,
                "basis": "candidate_firmware",
                "installed_semantics": "unverified",
                "physical_meaning": "unknown",
            }
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


@dataclass(frozen=True)
class _PendingWrite:
    """Own one encoded payload and its source-loop completion until delivery."""

    line: bytes
    size: int
    generation: int
    loop: asyncio.AbstractEventLoop
    receipt: Future[PersistenceResult]
    on_complete: Callable[[PersistenceResult], None] | None


@dataclass(frozen=True)
class _SyncRequest:
    """Order one receipt-draining synchronization barrier in the append queue."""

    result: Future[bool]
    receipts: tuple[Future[PersistenceResult], ...]
    stop: bool = False


class LocalDongleTraceWriter:
    """Append bounded JSONL off-loop, with explicit actual-persistence receipts."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_files: int | None = DEFAULT_MAX_FILES,
        session_id: str | None = None,
        max_queue_records: int = 1024,
        max_queue_bytes: int = 8 * 1024 * 1024,
        write_guard: Callable[[Path, int], AbstractContextManager[None]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.session_id = session_id if session_id is not None else str(uuid4())
        self.max_bytes = max(1, int(max_bytes))
        self.max_files = None if max_files is None else max(2, int(max_files))
        self._write_guard = write_guard
        self.max_queue_records = max(1, int(max_queue_records))
        self.max_queue_bytes = max(1, int(max_queue_bytes))
        self._queue: queue.SimpleQueue[_PendingWrite | _SyncRequest] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._close_result = False
        self._outstanding: set[Future[PersistenceResult]] = set()
        self._queued_bytes = 0
        self._dropped_records = 0
        self._write_errors = 0
        self._persisted_records = 0
        self._last_success: str | None = None
        self._last_error: str | None = None
        self._loss_generation = 0
        self._recovered_generation = -1
        self._dirty_paths: set[Path] = set()
        self._thread = threading.Thread(
            target=self._writer_loop, name="eg4-dongle-trace", daemon=True,
        )
        self._thread.start()

    @property
    def active_path(self) -> Path:
        """Return the current destination, without claiming that it exists."""
        return self.root / "local-dongle.jsonl"

    @property
    def last_error(self) -> str | None:
        """Return the historical last error, retained after later recovery."""
        with self._lock:
            return self._last_error

    @property
    def health(self) -> dict[str, Any]:
        """Return fresh thread-safe cumulative health, without filesystem access."""
        with self._lock:
            state = (
                "degraded" if self._loss_generation > self._recovered_generation
                and self._loss_generation > 0 else
                "healthy" if self._persisted_records else "starting"
            )
            return {
                "State": state,
                "QueueDepth": len(self._outstanding),
                "QueuedBytes": self._queued_bytes,
                "DroppedRecords": self._dropped_records,
                "WriteErrors": self._write_errors,
                "PersistedRecords": self._persisted_records,
                "LastSuccessfulPersistence": self._last_success,
                "LastError": self._last_error,
                "LossGeneration": self._loss_generation,
            }

    def _record_loss_locked(self, error: str, *, dropped: bool) -> None:
        """Accumulate loss while retaining the historical cause after recovery."""
        if dropped:
            self._dropped_records += 1
        else:
            self._write_errors += 1
        self._loss_generation += 1
        self._last_error = error

    async def async_write(
        self,
        record: dict[str, Any],
        *,
        wait: bool = True,
        on_complete: Callable[[PersistenceResult], None] | None = None,
    ) -> Path | None:
        """Accept bounded output promptly; optionally await its actual receipt.

        A wait=False path is only a destination. Accepted output and callbacks
        survive cancellation of the caller, but not arbitrary host power loss.
        """
        loop = asyncio.get_running_loop()
        enriched = {
            "schema": LOCAL_DONGLE_TRACE_SCHEMA,
            "kind": "acquisition",
            "session_id": self.session_id,
            **record,
            "queued_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        try:
            line = (
                json.dumps(enriched, separators=(",", ":"), allow_nan=False) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as err:
            with self._lock:
                self._record_loss_locked(f"{type(err).__name__}: {err}", dropped=True)
            return None
        # Account for the exact additional prefix written off-loop, without
        # retaining a second large serialized payload for byte accounting.
        size = len(line) + _PERSISTENCE_PREFIX_BYTES
        receipt: Future[PersistenceResult] = Future()
        with self._lock:
            error = (
                "trace writer is closed" if self._closed else
                "trace writer queue record limit reached"
                if len(self._outstanding) >= self.max_queue_records else
                "trace writer queue byte limit reached"
                if self._queued_bytes + size > self.max_queue_bytes else None
            )
            if error is not None:
                self._record_loss_locked(error, dropped=True)
                return None
            self._outstanding.add(receipt)
            self._queued_bytes += size
            self._queue.put(_PendingWrite(
                line, size, self._loss_generation, loop, receipt, on_complete,
            ))
        if not wait:
            return self.active_path
        result = await asyncio.shield(asyncio.wrap_future(receipt))
        return result.path

    async def async_sync(self) -> bool:
        """Drain preceding output/callbacks and fsync owned files off-loop."""
        with self._lock:
            closed = self._closed
            receipts = tuple(self._outstanding)
            result: Future[bool] = Future()
            if not closed:
                self._queue.put(_SyncRequest(result, receipts))
        if closed:
            await self.async_close()
            return self._close_result
        return await asyncio.shield(asyncio.wrap_future(result))

    async def async_close(self) -> None:
        """Idempotently drain, deliver receipts and synchronize even if cancelled."""
        if self._close_task is None:
            with self._lock:
                self._closed = True
                receipts = tuple(self._outstanding)
                result: Future[bool] = Future()
                self._queue.put(_SyncRequest(result, receipts, stop=True))
            self._close_task = asyncio.create_task(self._finish_close(result))
        await asyncio.shield(self._close_task)

    async def _finish_close(self, result: Future[bool]) -> None:
        """Finish the owned drain/sync task independently of caller cancellation."""
        self._close_result = await asyncio.shield(asyncio.wrap_future(result))
        await asyncio.to_thread(self._thread.join)

    def _deliver_completion(
        self, pending: _PendingWrite, result: PersistenceResult,
    ) -> None:
        """Deliver state mutation only on the originating event loop."""
        try:
            if pending.on_complete is not None:
                pending.on_complete(result)
        except Exception as err:
            pending.loop.call_exception_handler({
                "message": "EG4 persistence completion callback failed",
                "exception": err,
            })
        finally:
            with self._lock:
                self._outstanding.remove(pending.receipt)
                self._queued_bytes -= pending.size
            pending.receipt.set_result(result)

    def _writer_loop(self) -> None:
        """Serialize append and fsync operations on the one writer thread."""
        while True:
            pending = self._queue.get()
            if isinstance(pending, _SyncRequest):
                # Completion mutates journal/runtime state on its source loop.
                # The barrier must deliver those receipts before fsync starts.
                writes_ok = True
                for receipt in pending.receipts:
                    outcome = receipt.result()
                    writes_ok = (
                        writes_ok and outcome.path is not None
                        and outcome.error is None
                    )
                try:
                    self._sync_sync()
                except OSError as err:
                    with self._lock:
                        self._record_loss_locked(
                            f"{type(err).__name__}: {err}", dropped=False,
                        )
                    pending.result.set_result(False)
                else:
                    pending.result.set_result(writes_ok)
                if pending.stop:
                    return
                continue
            path = None
            error = None
            try:
                path = self._write_sync(pending.line)
            except OSError as err:
                error = f"{type(err).__name__}: {err}"
            completed_at = datetime.now(UTC).isoformat(timespec="milliseconds")
            with self._lock:
                if error is not None:
                    self._record_loss_locked(error, dropped=False)
                else:
                    self._persisted_records += 1
                    self._last_success = completed_at
                    self._recovered_generation = max(
                        self._recovered_generation, pending.generation,
                    )
            result = PersistenceResult(path, error, completed_at)
            pending.loop.call_soon_threadsafe(self._deliver_completion, pending, result)
            del pending

    def _write_sync(self, line: bytes) -> Path:
        """Append and flush without copying the encoded evidence payload."""
        started = datetime.now(UTC).isoformat(timespec="milliseconds").encode("ascii")
        prefix = b'{"persistence_started_at":"' + started + b'",'
        active = self.active_path
        guard = self._write_guard(active, len(prefix) + len(line) - 1) if self._write_guard else nullcontext()
        with guard:
            self.root.mkdir(parents=True, exist_ok=True)
            if (
                active.exists() and active.stat().st_size > 0
                and active.stat().st_size + len(prefix) + len(line) - 1 > self.max_bytes
            ):
                rotated = self.root / f"local-dongle-{time.time_ns()}.jsonl"
                active.replace(rotated)
                if active in self._dirty_paths:
                    self._dirty_paths.remove(active)
                    self._dirty_paths.add(rotated)
            else:
                self._append_line_sync(active, prefix, line)
                return active
        # Rotation changes the active inode. Re-enter the guard sequentially,
        # not recursively: custom guards need not use a reentrant lock.
        append_guard = self._write_guard(active, len(prefix) + len(line) - 1) if self._write_guard else nullcontext()
        with append_guard:
            self.root.mkdir(parents=True, exist_ok=True)
            self._append_line_sync(active, prefix, line)
        return active

    def _append_line_sync(self, active: Path, prefix: bytes, line: bytes) -> None:
        """Append one encoded record while its worker-side guard owns the file."""
        with active.open("ab") as stream:
            self._dirty_paths.add(active)
            stream.write(prefix)
            stream.write(memoryview(line)[1:])
            stream.flush()
        self._prune_sync()

    def _sync_sync(self) -> None:
        """Synchronize retained changed files and their directory off-loop."""
        if not self._dirty_paths:
            return
        for path in self._dirty_paths:
            try:
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            except FileNotFoundError:
                if self._write_guard is None:
                    raise
                # The external retention owner may expire a flushed segment
                # before the next barrier; it owns that deliberate coverage loss.
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._dirty_paths.clear()

    def _prune_sync(self) -> None:
        """Prune only owned trace segments and forget deliberately removed files."""
        if self.max_files is None:
            return
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
            self._dirty_paths.discard(path)


class ControllerObservationJournal:
    """Join discrete observations to explicit write/loss acknowledgements."""

    def __init__(self, writer: LocalDongleTraceWriter) -> None:
        self.writer = writer
        self._tracker = ControllerObservationTracker()
        self._writer_loss_generation = 0
        self._health_callback: Callable[[], None] | None = None
        self._observation_callback: Callable[[dict[str, Any]], None] | None = None
        self._last_transition: tuple[str, bool] | None = None

    def _refresh_writer_loss(self) -> dict[str, Any]:
        """Turn writer loss, including fsync failure, into journal discontinuity."""
        health = self.writer.health
        if health["LossGeneration"] > self._writer_loss_generation:
            self._writer_loss_generation = health["LossGeneration"]
            self._tracker.mark_loss()
        return health

    @property
    def health(self) -> dict[str, Any]:
        """Expose actual storage and continuity, including outstanding recovery."""
        health = {**self._refresh_writer_loss(), **self._tracker.health}
        if health["ContinuityBroken"]:
            health["State"] = "degraded"
        return health

    def set_observation_callback(
        self, callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        """Deliver each discrete observation on its source loop before enqueue."""
        self._observation_callback = callback

    def set_health_callback(self, callback: Callable[[], None] | None) -> None:
        """Notify only significant state/continuity transitions on this loop."""
        self._health_callback = callback
        health = self.health
        self._last_transition = (health["State"], health["ContinuityBroken"])

    def _notify_transition(self) -> None:
        """Wake health publication only for current state/continuity transitions."""
        health = self.health
        transition = (health["State"], health["ContinuityBroken"])
        if transition != self._last_transition:
            self._last_transition = transition
            if self._health_callback is not None:
                self._health_callback()

    async def async_observe(self, record: dict[str, Any]) -> None:
        """Enqueue discrete evidence without waiting for the disk or a receipt."""
        self._refresh_writer_loss()
        observation = self._tracker.observe(record)
        if observation is not None:
            if self._observation_callback is not None:
                try:
                    self._observation_callback(observation)
                except Exception as err:
                    asyncio.get_running_loop().call_exception_handler({
                        "message": "EG4 observation callback failed",
                        "exception": err,
                    })
            token = self._tracker.mark_pending(observation)

            def completed(result: PersistenceResult) -> None:
                self._refresh_writer_loss()
                self._tracker.acknowledge(token, result)
                self._notify_transition()

            accepted = await self.writer.async_write(
                observation, wait=False, on_complete=completed,
            )
            if accepted is None:
                self._tracker.reject(token)
        self._notify_transition()
