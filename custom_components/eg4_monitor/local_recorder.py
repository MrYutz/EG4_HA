"""Single local acquisition owner, with independently scheduled device roles.

Transaction completion, not cache/consumer time, determines freshness. A round
is a sequence of independently timed blocks, never an atomic site snapshot.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from statistics import median
from typing import Any
from uuid import uuid4

from .const import (
    LOCAL_DONGLE_TRACE_SCHEMA,
    LOCAL_GRIDBOSS_DYNAMIC_BLOCKS,
    LOCAL_GRIDBOSS_MAINTENANCE,
    LOCAL_GRIDBOSS_PRIMARY_BLOCKS,
    LOCAL_INVERTER_DYNAMIC_BLOCKS,
    LOCAL_INVERTER_MAINTENANCE,
    LOCAL_INVERTER_PRIMARY_BLOCKS,
)
from .eg4_logger import LoggerSample
from .local_dongle import (
    EG4GridBossTelemetry,
    EG4LocalBMSData,
    EG4LocalDongle,
    EG4LocalTelemetry,
    merge_local_telemetry,
)
from .local_dongle_trace import (
    LocalDongleTraceWriter,
    gridboss_to_record,
    telemetry_to_record,
)

Shape = tuple[int, int, int]


def _age(source: dict[str, Any] | None, now: datetime) -> float | None:
    """Return source age without turning a cache read into a fresh observation."""
    if not source or not source.get("acquired_at"):
        return None
    return max(0.0, (now - datetime.fromisoformat(source["acquired_at"])).total_seconds())


def _key(shape: Shape) -> str:
    """Name a read shape consistently in persisted context and health."""
    return ":".join(str(part) for part in shape)


@dataclass(frozen=True)
class RecorderSnapshot:
    """Current usable values and original, independently aged provenance."""

    inverters: dict[str, EG4LocalTelemetry]
    gridboss: EG4GridBossTelemetry | None
    sample: LoggerSample
    local_freshness: dict[str, Any]
    gridboss_freshness: dict[str, Any]
    health: dict[str, Any]
    settings: dict[str, Any]


@dataclass
class _Role:
    """Retain one role's scheduler, latest attempts and last-good evidence."""

    device: EG4LocalDongle
    primary: tuple[Shape, ...]
    dynamic: tuple[Shape, ...]
    periods: dict[Shape, float]
    due: dict[Shape, float]
    blocks: dict[Shape, dict[str, Any]] = field(default_factory=dict)
    last_good: dict[Shape, dict[str, Any]] = field(default_factory=dict)
    core: EG4LocalTelemetry | EG4GridBossTelemetry | None = None
    source: dict[str, Any] | None = None
    primary_available: bool = False
    bms: EG4LocalBMSData | None = None
    bms_available: bool = False
    bms_source: dict[str, Any] | None = None
    last_attempt: str | None = None
    last_success: str | None = None
    last_started: float | None = None
    spacing: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    error: str | None = None
    callback_error: str | None = None
    errors: int = 0
    sequence: int = 0
    source_sequence: int | None = None
    last_transfer_state: str | None = None
    transfer_unknown: bool = False


class EG4LocalRecorder:
    """Own all local capture, profile deadlines, cache and freshness policy.

    ``clock`` is a monotonic scheduling dependency only; source wall times always
    come from the actual transport. The caller, not this class, closes the writer.
    """

    def __init__(
        self,
        *,
        devices: dict[str, EG4LocalDongle],
        configured_inverter_roles: set[str],
        writer: LocalDongleTraceWriter | None,
        on_update: Callable[[dict[str, Any] | None], Awaitable[None]],
        interval: float = 1.0,
        bms_interval: float = 5.0,
        build_id: str = "unknown",
        session_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind independent role owners and their shared evidence consumer."""
        self.interval = max(0.001, float(interval))
        self.bms_interval = max(0.001, float(bms_interval))
        self.max_age = max(15.0, self.interval * 5.0)
        self.writer = writer
        self.session_id = session_id or (writer.session_id if writer else str(uuid4()))
        self.build_id = build_id
        self._on_update = on_update
        self._clock = clock
        self._configured = set(configured_inverter_roles) | (devices.keys() - {"gridboss"})
        self._roles: dict[str, _Role] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self._started = False
        self._sequence = 0
        self._health_cache: dict[str, Any] | None = None
        self._health_at = 0.0
        self._writer_error: str | None = None
        for role, device in devices.items():
            gridboss = role == "gridboss"
            profile = LOCAL_GRIDBOSS_MAINTENANCE if gridboss else LOCAL_INVERTER_MAINTENANCE
            periods = {
                (fn, start, count): self.bms_interval if period is None else period
                for fn, start, count, period in profile
            }
            self._roles[role] = _Role(
                device=device,
                primary=LOCAL_GRIDBOSS_PRIMARY_BLOCKS if gridboss else LOCAL_INVERTER_PRIMARY_BLOCKS,
                dynamic=LOCAL_GRIDBOSS_DYNAMIC_BLOCKS if gridboss else LOCAL_INVERTER_DYNAMIC_BLOCKS,
                periods=periods,
                due=dict.fromkeys(periods, self._clock()),
            )
            device.role = role
            device.session_id = self.session_id
            device.evidence_sink = self._observe

    async def async_start(self) -> None:
        """Start exactly one task per role without waiting for device responses."""
        if self._started or self._closed:
            return
        self._started = True
        for role in self._roles:
            self._tasks[role] = asyncio.create_task(self._run(role), name=f"eg4-recorder-{role}")

    async def async_close(self) -> None:
        """Cancel and join only owned tasks; leave writer draining to the caller."""
        self._closed = True
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        for state in self._roles.values():
            state.primary_available = False
        self._health_cache = None

    async def _write(self, record: dict[str, Any]) -> None:
        """Enqueue evidence without allowing writer failure to stop acquisition."""
        if self.writer is None:
            return
        try:
            accepted = await self.writer.async_write(record, wait=False)
            error = self.writer.last_error if accepted is None else None
            if accepted is None and error is None:
                error = "trace writer rejected record"
        except Exception as err:
            error = f"{type(err).__name__}: {err}"
        if error != self._writer_error:
            self._writer_error = error
            self._health_cache = None

    async def _observe(self, evidence: dict[str, Any]) -> None:
        """Keep bounded shape metadata before optionally persisting raw evidence."""
        state = self._roles.get(evidence["role"])
        if state is not None and evidence.get("kind") == "transaction":
            request = evidence["request"]
            shape = (request["function"], request["register"], request["count"])
            if shape in (*state.primary, *state.dynamic, *state.periods):
                matched = next(
                    (frame for frame in reversed(evidence["frames"])
                     if frame.get("classification") == "matching"),
                    {},
                )
                state.blocks[shape] = {
                    **{name: evidence[name] for name in (
                        "session_id", "acquisition_id", "transaction_id", "started_at", "completed_at", "ok",
                    )},
                    "function": shape[0], "register": shape[1], "count": shape[2],
                    "acquired_at": evidence["completed_at"],
                    "values": matched.get("values") if evidence["ok"] else None,
                    "validation": dict(matched.get("validation", {})),
                    "semantic": "not_evaluated",
                    "error": evidence.get("error"),
                }
        elif state is not None and evidence.get("kind") == "interpretation":
            for block in state.blocks.values():
                if block["transaction_id"] == evidence["transaction_id"]:
                    block["semantic"] = evidence["semantic"]
                    block["semantic_error"] = evidence.get("error")
                    break
        await self._write(evidence)

    def _source(self, state: _Role, shapes: tuple[Shape, ...], acquisition_id: str) -> dict[str, Any]:
        """Require successful primary blocks from this exact acquisition."""
        blocks = [state.blocks.get(shape) for shape in shapes]
        if not all(block and block["ok"] and block["acquisition_id"] == acquisition_id for block in blocks):
            raise ValueError("Primary acquisition provenance unavailable")
        transactions = [{name: block[name] for name in (
            "session_id", "acquisition_id", "transaction_id", "started_at", "completed_at", "ok",
        )} for block in blocks if block is not None]
        return {
            "acquisition_id": acquisition_id,
            "transaction_ids": [block["transaction_id"] for block in transactions],
            "started_at": min(block["started_at"] for block in transactions),
            "completed_at": max(block["completed_at"] for block in transactions),
            "acquired_at": min(block["completed_at"] for block in transactions),
            "transactions": transactions,
        }

    async def _notify(self, state: _Role, record: dict[str, Any] | None) -> None:
        """Isolate consumer failures and expose them through recorder health."""
        try:
            await self._on_update(record)
        except Exception as err:
            error = f"{type(err).__name__}: {err}"
            state.errors += 1
        else:
            error = None
        if state.callback_error != error:
            state.callback_error = error
            self._health_cache = None

    async def _primary(self, role: str, state: _Role, record: dict[str, Any]) -> None:
        """Read dynamic data and publish primary continuity loss immediately."""
        try:
            if role == "gridboss":
                core = await state.device.async_read_snapshot()
                if not isinstance(core, EG4GridBossTelemetry):
                    raise ValueError("Expected GridBOSS telemetry")
            else:
                core = await state.device.async_read_core_snapshot()
            source = self._source(state, state.primary, record["acquisition_id"])
        except Exception as err:
            state.error = f"{type(err).__name__}: {err}"
            state.errors += 1
            state.primary_available = False
            record["error"] = state.error
            self._health_cache = None
            # Publish the interruption before any later block or recovery can
            # hide a failed observation behind a still-young last-good cache.
            await self._notify(state, None)
        else:
            if not state.primary_available or state.error is not None:
                self._health_cache = None
            state.core, state.source = core, source
            state.source_sequence = state.sequence
            state.primary_available = True
            state.error = None
            state.last_success = source["acquired_at"]
            record["ok"] = True
            record["primary_source"] = deepcopy(source)
            record["primary_sample_completed_at"] = source["acquired_at"]

    async def _block(self, state: _Role, shape: Shape, *, bms: bool = False) -> None:
        """Attempt one maintenance shape without replacing last-good on failure."""
        previous = state.last_good.get(shape)
        previous_attempt = state.blocks.get(shape)
        bms_data: EG4LocalBMSData | None = None
        try:
            if bms:
                bms_data = await state.device.async_read_bms_snapshot()
            else:
                await state.device.async_read_register_block(shape[1], shape[2], function=shape[0])
            block = state.blocks[shape]
            if not block["ok"]:
                raise ValueError("Block acquisition provenance unavailable")
        except Exception as err:
            state.errors += 1
            self._health_cache = None
            if bms:
                state.bms_available = False
            # Transport retains the original failed transaction. A semantic
            # rejection is separate from structurally valid raw words.
            block = state.blocks.get(shape)
            if block is not None:
                block["error"] = f"{type(err).__name__}: {err}"
        else:
            if previous_attempt is None or not previous_attempt["ok"] or previous_attempt.get("error"):
                self._health_cache = None
            if shape in state.periods:
                observed_changed = previous is not None and previous["values"] != block["values"]
                state.last_good[shape] = {**deepcopy(block), "observed_changed": observed_changed}
            if bms_data is not None:
                state.bms = bms_data
                state.bms_available = bms_data.available
                state.bms_source = self._source(state, (shape,), block["acquisition_id"])

    async def _run(self, role: str) -> None:
        """Run one role's primary cadence and oldest-due maintenance work."""
        state = self._roles[role]
        while not self._closed:
            started = time.monotonic()
            with state.device.capture() as acquisition:
                self._sequence += 1
                state.sequence = self._sequence
                gap = None if state.last_started is None else max(0.0, started - state.last_started)
                if gap is not None:
                    state.spacing.append(gap)
                state.last_started = started
                state.last_attempt = datetime.now(UTC).isoformat()
                record: dict[str, Any] = {
                    "schema": LOCAL_DONGLE_TRACE_SCHEMA, "kind": "acquisition",
                    "session_id": self.session_id, "build_id": self.build_id,
                    **acquisition, "sequence": state.sequence, "role": role,
                    "host": state.device.host, "port": state.device.port,
                    "sample_started_at": state.last_attempt,
                    "sample_gap_seconds": gap, "ok": False,
                }
                cancelled = False
                try:
                    await self._primary(role, state, record)
                    for shape in state.dynamic:
                        await self._block(state, shape)
                    due = [shape for shape, deadline in state.due.items() if deadline <= self._clock()]
                    if due:
                        shape = min(due, key=state.due.__getitem__)
                        # Schedule from actual attempt, including failures. Ties
                        # retain profile order; startup shapes cannot starve.
                        state.due[shape] = self._clock() + state.periods[shape]
                        await self._block(state, shape, bms=shape == (4, 80, 40))
                        record["maintenance_attempted"] = _key(shape)
                except asyncio.CancelledError:
                    cancelled = True
                    state.primary_available = False
                    record["error"] = "CancelledError: acquisition cancelled"
                    self._health_cache = None
                finally:
                    self._finish(state, record)
                    await self._write(record)
                    if not cancelled:
                        await self._notify(state, record)
                if cancelled:
                    raise asyncio.CancelledError
            await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - started)))

    def _context(self, state: _Role, shape: Shape, now: datetime) -> dict[str, Any]:
        """Keep latest-attempt failure distinct from aged last-success context."""
        latest = state.blocks.get(shape)
        last_good = state.last_good.get(shape)
        return {
            "function": shape[0], "register": shape[1], "count": shape[2],
            "target_interval_seconds": state.periods[shape],
            "status": "missing" if latest is None else "ok" if (
                latest["ok"] and latest["semantic"] != "rejected"
            ) else "failed",
            "latest_attempt": {**deepcopy(latest), "age_seconds": _age(latest, now)} if latest else None,
            "last_success": {**deepcopy(last_good), "age_seconds": _age(last_good, now)} if last_good else None,
            "effective_change_time_known": False,
        }

    def _configuration(self, state: _Role, now: datetime) -> dict[str, Any]:
        """Expose only the bounded holding-register configuration profile."""
        return {_key(shape): self._context(state, shape, now) for shape in state.periods if shape[0] == 3}

    def _bms_context(self, state: _Role, now: datetime) -> dict[str, Any]:
        """Report BMS source identity, original age and latest attempt status."""
        shape = (4, 80, 40)
        if shape not in state.periods:
            return {}
        age = _age(state.bms_source, now)
        return {
            **self._context(state, shape, now),
            "source": deepcopy(state.bms_source), "age_seconds": age,
            "available": state.bms_available and age is not None and age <= self.max_age,
        }

    def _finish(self, state: _Role, record: dict[str, Any]) -> None:
        """Finish one role record with current raw blocks and cached provenance."""
        now = datetime.now(UTC)
        acquisition_id = record["acquisition_id"]
        current = [block for block in state.blocks.values() if block["acquisition_id"] == acquisition_id]
        record["primary_blocks"] = [
            {**deepcopy(state.blocks[shape]), "quality": {
                "structural": "accepted" if state.blocks[shape]["ok"] else "rejected",
                "semantic": state.blocks[shape]["semantic"],
            }}
            for shape in state.primary if shape in state.blocks
            and state.blocks[shape]["acquisition_id"] == acquisition_id
        ]
        record["diagnostics"] = [
            {**deepcopy(block), "age_seconds": _age(block, now),
             "current_acquisition": block["acquisition_id"] == acquisition_id}
            for shape, block in state.blocks.items()
            if shape in state.dynamic or shape in state.periods and shape[0] == 3
        ]
        record["configuration_context"] = self._configuration(state, now)
        record["bms_context"] = self._bms_context(state, now)
        record["bms_source"] = {
            **deepcopy(state.bms_source or {}),
            "used_in_telemetry": record["ok"] and record["bms_context"].get("available", False),
        }
        record["bms_age_seconds"] = record["bms_context"].get("age_seconds")
        record["bms_sampled"] = any(block["register"] == 80 and block["function"] == 4 for block in current)
        if record["ok"] and state.core is not None:
            if isinstance(state.core, EG4GridBossTelemetry):
                record["telemetry"] = gridboss_to_record(state.core)
                transfer = state.core.transfer_state
                record["transfer_transition"] = (
                    {"from": state.last_transfer_state, "to": transfer,
                     "intervening_unknown": state.transfer_unknown}
                    if state.last_transfer_state is not None
                    and state.last_transfer_state != transfer else None
                )
                state.last_transfer_state = transfer
                state.transfer_unknown = False
            else:
                core = merge_local_telemetry(state.core, state.bms) if record["bms_context"]["available"] else state.core
                record["telemetry"] = telemetry_to_record(core)
        elif isinstance(state.core, EG4GridBossTelemetry):
            state.transfer_unknown = True
        record["errors"] = [
            {"function": block["function"], "register": block["register"],
             "count": block["count"], "transaction_id": block["transaction_id"],
             "error": block.get("semantic_error") or block.get("error")}
            for block in current if not block["ok"] or block.get("error") or block["semantic"] == "rejected"
        ]
        record["partial"] = bool(record["successful_transaction_ids"]) and (
            not record["ok"] or bool(record["errors"])
        )
        record["sample_completed_at"] = now.isoformat()
        record["duration_ms"] = max(0.0, (now - datetime.fromisoformat(record["sample_started_at"])).total_seconds() * 1000)

    def _health(self, now: datetime) -> dict[str, Any]:
        """Summarize role continuity and measured cadence at bounded frequency."""
        clock = time.monotonic()
        if self._health_cache is not None and clock - self._health_at < 30.0:
            return deepcopy(self._health_cache)
        roles = {}
        for role in sorted(self._configured | self._roles.keys()):
            state = self._roles.get(role)
            if state is None:
                roles[role] = {"State": "degraded", "PrimaryAvailable": False, "LastError": "configured driver missing"}
                continue
            maintenance = {_key(shape): self._context(state, shape, now) for shape in state.periods}
            missing = [key for key, context in maintenance.items() if context["status"] == "missing"]
            failed = [key for key, context in maintenance.items() if context["status"] == "failed"]
            missing.extend(
                _key(shape) for shape in (*state.primary, *state.dynamic)
                if shape not in state.blocks
            )
            failed.extend(
                _key(shape) for shape in (*state.primary, *state.dynamic)
                if shape in state.blocks and (
                    not state.blocks[shape]["ok"]
                    or state.blocks[shape]["semantic"] == "rejected"
                    or state.blocks[shape].get("error")
                )
            )
            diagnostics = [block for shape, block in state.blocks.items() if shape in state.dynamic]
            primary_age = _age(state.source, now)
            healthy = (state.primary_available and primary_age is not None and primary_age <= self.max_age
                       and not state.callback_error and not missing and not failed
                       and len(diagnostics) == len(state.dynamic) and all(block["ok"] for block in diagnostics))
            roles[role] = {
                "State": "starting" if state.last_attempt is None else "healthy" if healthy else "degraded",
                "LastAttempt": state.last_attempt, "LastSuccess": state.last_success,
                "PrimaryAvailable": state.primary_available,
                "PrimaryAgeSeconds": primary_age,
                "DiagnosticAgesSeconds": {_key(shape): _age(state.blocks.get(shape), now) for shape in state.dynamic},
                "MedianSpacingSeconds": median(state.spacing) if state.spacing else None,
                "MaxSpacingSeconds": max(state.spacing) if state.spacing else None,
                "SpacingSamples": len(state.spacing), "Errors": state.errors,
                "LastError": state.error, "CallbackError": state.callback_error,
                "MissingProfileBlocks": missing, "FailedProfileBlocks": failed,
                "Maintenance": {key: {
                    "Status": context["status"],
                    "LastSuccessAgeSeconds": (context["last_success"] or {}).get("age_seconds"),
                } for key, context in maintenance.items()},
            }
        overall = "starting" if not self._started else "healthy" if roles and all(
            role["State"] == "healthy" for role in roles.values()
        ) and not self._writer_error else "degraded"
        self._health_cache = {
            "SessionId": self.session_id, "BuildId": self.build_id, "State": overall,
            "ObservedAt": now.isoformat(), "Roles": roles, "WriterError": self._writer_error,
        }
        self._health_at = clock
        return deepcopy(self._health_cache)

    def snapshot(self, now: datetime | None = None) -> RecorderSnapshot:
        """Consume last-good values without renewing identity, age or availability."""
        now = now or datetime.now(UTC)
        inverters: dict[str, EG4LocalTelemetry] = {}
        ages: dict[str, float] = {}
        sources = {}
        bms_sources = {}
        primary_available = {}
        role_context = {}
        for role in sorted(self._configured):
            state = self._roles.get(role)
            source = state.source if state else None
            age = _age(source, now)
            core = state.core if state else None
            available = bool(state and state.primary_available and age is not None and age <= self.max_age)
            primary_available[role] = bool(state and state.primary_available)
            if age is not None:
                ages[role] = age
            if source is not None:
                sources[role] = deepcopy(source)
            if state and state.bms_source:
                bms_sources[role] = {**deepcopy(state.bms_source), **self._bms_context(state, now)}
            if available and state is not None and isinstance(core, EG4LocalTelemetry):
                bms_context = self._bms_context(state, now)
                inverters[role] = merge_local_telemetry(core, state.bms) if bms_context["available"] else core
            power = core.charge_power if isinstance(core, EG4LocalTelemetry) else None
            valid_power = power is not None and isfinite(power) and power >= 0
            role_context[role] = {
                **deepcopy(source or {}), "available": available and valid_power,
                "acquisition_id": (source or {}).get("acquisition_id"),
                "acquired_at": (source or {}).get("acquired_at"), "age_seconds": age,
                "charge_power_w": power if valid_power else None,
            }
        grid_state = self._roles.get("gridboss")
        grid_source = grid_state.source if grid_state else None
        grid_age = _age(grid_source, now)
        gridboss = grid_state.core if (grid_state and grid_state.primary_available
            and grid_age is not None and grid_age <= self.max_age
            and isinstance(grid_state.core, EG4GridBossTelemetry)) else None
        charge_available = bool(role_context) and all(context["available"] for context in role_context.values())
        process_charge = bool(self._roles.keys() - {"gridboss"})
        sample = LoggerSample(
            observed_at=now,
            generator_acquisition_id=(grid_source or {}).get("acquisition_id"),
            generator_acquired_at=datetime.fromisoformat(grid_source["acquired_at"]) if grid_source else None,
            charge_acquisition_id="|".join(f"{role}:{context['acquisition_id']}" for role, context in sorted(role_context.items())) if charge_available else None,
            charge_acquired_at=max(datetime.fromisoformat(context["acquired_at"]) for context in role_context.values()) if charge_available else None,
            charge_role_context=role_context, sequence=self._sequence or None,
            source_serial=grid_state.core.inverter_serial if grid_state and grid_state.core else None,
            charge_source_serial=next((core.inverter_serial for core in inverters.values() if core.inverter_serial), None),
            generator_source_available=gridboss is not None,
            generator_input_registers_valid=bool(gridboss and gridboss.input_registers_valid),
            generator_voltage=gridboss.generator_voltage if gridboss else None,
            generator_frequency=gridboss.generator_frequency if gridboss else None,
            generator_power=gridboss.generator_power if gridboss else None,
            transfer_state=gridboss.transfer_state if gridboss else None,
            charge_source_available=charge_available and process_charge,
            charge_power_w=sum(context["charge_power_w"] for context in role_context.values()) if charge_available and process_charge else None,
            charge_source="local_flexboss" if process_charge else None,
            battery_voltage=next((core.battery_voltage for core in inverters.values() if core.battery_voltage is not None), None),
            battery_soc=next((core.battery_soc for core in inverters.values() if core.battery_soc is not None), None),
            process_generator=grid_state is not None, process_charge=process_charge,
        )
        return RecorderSnapshot(
            inverters=inverters, gridboss=gridboss, sample=sample,
            local_freshness={
                "Enabled": self.writer is not None, "IntervalSeconds": self.interval,
                "BMSIntervalSeconds": self.bms_interval, "MaxAgeSeconds": self.max_age,
                "SampleAgesSeconds": ages,
                "StaleRoles": sorted(role for role, age in ages.items() if age > self.max_age),
                "MissingRoles": sorted(self._configured - sources.keys()),
                "AvailableRoles": sorted(inverters), "PrimaryAvailable": primary_available,
                "IsStale": process_charge and not self._configured <= inverters.keys(),
                "Sources": sources, "BMSSources": bms_sources,
            },
            gridboss_freshness={
                "Available": gridboss is not None, "SampleAgeSeconds": grid_age,
                "Host": grid_state.device.host if grid_state else None,
                "Sequence": grid_state.source_sequence if grid_state else None,
                "LastError": grid_state.error if grid_state else None,
                "Source": deepcopy(grid_source),
            },
            health=self._health(now),
            settings={role: self._configuration(state, now) for role, state in self._roles.items()},
        )


__all__ = ["EG4LocalRecorder", "RecorderSnapshot"]
