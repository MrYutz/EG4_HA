"""Bounded, received-evidence incident bundles and recorder-owned retention.

Acquisition calls only mutate bounded source-loop memory. A single spool task
feeds the existing threaded JSONL writer; all ownership, retention, manifests,
coverage inspection and hashing run off-loop under one native filesystem lock.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .eg4_logger import (
    GENERATOR_OFF_FREQUENCY,
    GENERATOR_OFF_POWER,
    GENERATOR_OFF_VOLTAGE,
    GENERATOR_ON_FREQUENCY,
    GENERATOR_ON_POWER,
    GENERATOR_ON_VOLTAGE,
)
from .local_dongle import raw_primary_measurements
from .local_dongle_trace import LocalDongleTraceWriter

_RECORDER_SCHEMA = "eg4_host_recorder.v1"
_INCIDENT_SCHEMA = "eg4_host_incident.v1"
_SEGMENT = re.compile(r"local-dongle(?:-[0-9]+)?\.jsonl\Z")
_INCIDENT_DIR = re.compile(r"incident-[A-Za-z0-9_-]+\Z")
_MAX_SUMMARIES = 64
_MAX_RECLAMATION_METADATA_BYTES = 1024**2
_MONITORED_MEASUREMENTS = frozenset({
    "grid_voltage_v", "grid_frequency_hz", "generator_voltage_v",
    "generator_frequency_hz", "generator_power_w", "ups_voltage_v",
    "eps_voltage_r_v", "eps_frequency_hz",
})


@dataclass(frozen=True)
class RecorderLimits:
    """Ceilings, not preallocation or promises of minimum retained history."""

    raw_max_bytes: int = 2 * 1024**3
    raw_max_age_seconds: float = 72 * 3600
    journal_max_bytes: int = 200 * 1024**2
    journal_max_age_seconds: float = 14 * 86400
    incident_max_bytes: int = 1024**3
    incident_max_age_seconds: float = 14 * 86400
    min_free_bytes: int = 1024**3
    segment_bytes: int = 25 * 1024**2
    pre_seconds: float = 600
    post_seconds: float = 600
    pre_max_bytes: int = 16 * 1024**2
    max_queue_bytes: int = 32 * 1024**2
    max_queue_records: int = 32768

    def __post_init__(self) -> None:
        """Reject nonsensical limits without probing storage in construction."""
        for name, value in vars(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        if self.segment_bytes < 1 or self.max_queue_records < 1 or self.max_queue_bytes < 1:
            raise ValueError("segment and queue limits must be positive")


@dataclass(frozen=True)
class _Buffered:
    """Own an immutable encoded record, receive time and original interval."""

    line: bytes
    received_at: datetime
    start: str
    end: str
    role: str | None
    usable_primary: bool


@dataclass
class _Incident:
    """Keep only bounded summaries; individual events belong in trigger JSONL."""

    manifest: dict[str, Any]
    path: Path
    first_trigger: datetime
    last_trigger: datetime
    writers: dict[str, LocalDongleTraceWriter] = field(default_factory=dict)
    reason_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    observed_roles: set[str] = field(default_factory=set)
    coverage_by_role: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Work:
    """Order prebuffer, live evidence and drain barriers on one bounded spool."""

    incident: _Incident | None
    records: tuple[_Buffered, ...] = ()
    kind: str = "records"
    pre: bool = False
    done: asyncio.Future[None] | None = None


def _iso(value: datetime) -> str:
    """Normalize clock values to comparable UTC timestamps."""
    return value.astimezone(UTC).isoformat()


def _date(value: Any) -> datetime | None:
    """Parse a received timestamp without inventing one for unknown input."""
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _number(value: Any) -> float | None:
    """Keep missing and invalid measurements separate from measured zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _interval(record: dict[str, Any], fallback: datetime) -> tuple[str, str]:
    """Preserve source intervals rather than treating persistence time as sampling."""
    source = record.get("primary_source") or {}
    start = next((parsed for value in (
        record.get("started_at"), record.get("sample_started_at"),
        source.get("started_at"), record.get("observed_at"),
    ) if (parsed := _date(value)) is not None), fallback)
    end = next((parsed_end for value in (
        record.get("completed_at"), record.get("sample_completed_at"),
        source.get("completed_at"), record.get("observed_at"),
    ) if (parsed_end := _date(value)) is not None), start)
    return _iso(start), _iso(end)


def _bounded_add(items: list[Any], value: Any) -> None:
    """Retain bounded distinct summaries, with an explicit overflow marker."""
    if value in items:
        return
    if len(items) < _MAX_SUMMARIES:
        items.append(value)
    elif "additional_summaries_omitted" not in items:
        items[-1] = "additional_summaries_omitted"


class IncidentRecorder:
    """Preserve immediate all-role evidence without blocking role acquisition."""

    def __init__(
        self, root: str | Path, *, entry_id: str, session_id: str, build_id: str,
        roles: set[str], limits: RecorderLimits | None = None,
        clock: Callable[[], datetime] | None = None, evidence_class: str = "received",
    ) -> None:
        self.root = Path(root).absolute()
        self.raw_root = self.root / "raw"
        self.journal_root = self.root / "controller-observations"
        self.incidents_root = self.root / "incidents"
        self.entry_id, self.session_id, self.build_id = entry_id, session_id, build_id
        self.roles = frozenset(roles)
        self.limits = limits or RecorderLimits()
        self.evidence_class = evidence_class
        self._clock = clock or (lambda: datetime.now(UTC))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._health_callback: Callable[[], None] | None = None
        self._context_provider: Callable[[], dict[str, Any]] | None = None
        self._last_transition: tuple[Any, ...] | None = None
        self._started_at: datetime | None = None
        self._initialized = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._work: deque[_Work] = deque()
        self._queued_bytes = 0
        self._queued_records = 0
        self._buffer: deque[_Buffered] = deque()
        self._buffer_bytes = 0
        self._memory_lost_at: datetime | None = None
        self._active: _Incident | None = None
        self._latest: _Incident | None = None
        self._pending: dict[str, _Incident] = {}
        self._predicates: dict[str, Any] = {}
        self._active_anomalies: set[str] = set()
        self._transactions: OrderedDict[str, str] = OrderedDict()
        self._last_error: str | None = None
        self._current_error: str | None = None
        self._losses = 0
        self._guard_errors: set[str] = set()
        self._worker_failed_streams: set[str] = set()
        self._lost_bytes = 0
        self._own_loss = False
        self._session_gaps: list[Any] = []
        self._tick_lock = asyncio.Lock()
        self._owner_cache: dict[str, Any] | None = None
        self._owner_signature: tuple[int, int, int, int] | None = None
        self._fs_lock = threading.RLock()
        self._segment_cache: dict[Path, tuple[int, int, str | None, str | None]] = {}
        self._incident_segment_cache: dict[tuple[int, int], tuple[int, int, dict[str, Any]]] = {}
        self._manifest_cache: dict[Path, tuple[tuple[int, ...], dict[str, Any]]] = {}
        self._recognized_segments: dict[Path, tuple[int, int]] = {}
        self._protected_bundles: set[Path] = set()
        self._inventory_cache: dict[str, list[Path]] | None = None
        self._inventory_stamps: dict[Path, tuple[int, ...] | None] = {}
        self._metrics: dict[str, Any] = {
            "RawOldest": None, "RawNewest": None, "RawBytes": 0,
            "JournalBytes": 0, "IncidentBytes": 0, "FreeBytes": None,
            "EstimatedRawRetentionHours": None,
            "StorageObservedAt": None,
        }

    def set_health_callback(self, callback: Callable[[], None] | None) -> None:
        """Notify significant source-loop transitions, never each appended byte."""
        self._health_callback = callback
        self._last_transition = self._transition()

    def set_context_provider(self, callback: Callable[[], dict[str, Any]] | None) -> None:
        """Bind a source-loop configuration snapshot provider after construction."""
        self._context_provider = callback

    @property
    def health(self) -> dict[str, Any]:
        """Return cached actual storage and current bounded in-memory state."""
        now = self._now()
        coverage = self._pre_coverage(now)
        incident = self._active or self._latest
        manifest = incident.manifest if incident is not None else {}
        return {
            "State": "degraded" if self._current_error or self._own_loss else
            "healthy" if self._initialized else "starting",
            **self._metrics,
            "ActiveIncidentId": self._active.manifest["incident_id"] if self._active else None,
            "ActiveIncidentPath": str(self._active.path) if self._active else None,
            "LatestIncidentId": self._latest.manifest["incident_id"] if self._latest else None,
            "LatestIncidentPath": str(self._latest.path) if self._latest else None,
            "Incomplete": bool(manifest and not manifest.get("complete", False)),
            "Truncated": bool(manifest.get("truncated", False)) or (
                self._memory_lost_at is not None
                and (now - self._memory_lost_at).total_seconds() <= self.limits.pre_seconds
            ),
            "PrebufferBytes": self._buffer_bytes,
            "PrebufferCoverageSeconds": coverage,
            "WarmingUp": self._started_at is None or
            (now - self._started_at).total_seconds() < self.limits.pre_seconds,
            "LastError": self._last_error,
            "DroppedRecords": self._losses, "LostBytes": self._lost_bytes,
            "QueueDepth": self._queued_records, "QueuedBytes": self._queued_bytes,
            "ActiveAnomalies": sorted(self._active_anomalies),
            "Gaps": deepcopy(self._session_gaps),
        }

    def _now(self) -> datetime:
        """Read the injected receive clock, normalized independently of source time."""
        value = self._clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _transition(self) -> tuple[Any, ...]:
        """Select notification-worthy state without taking the filesystem lock."""
        current = self._active or self._latest
        return (
            self._initialized, bool(self._current_error or self._own_loss),
            self._active.manifest["incident_id"] if self._active else None,
            self._latest.manifest["incident_id"] if self._latest else None,
            bool(current and current.manifest.get("incomplete_reasons")),
            bool(current and current.manifest.get("truncated")),
        )

    def _notify(self) -> None:
        """Call an optional source-loop subscriber only on significant changes."""
        transition = self._transition()
        if transition != self._last_transition:
            self._last_transition = transition
            if self._health_callback is not None:
                self._health_callback()

    async def async_start(self) -> None:
        """Initialize owned storage and interrupt unfinished sessions off-loop."""
        if self._started_at is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._started_at = self._now()
        try:
            gaps, metrics = await asyncio.to_thread(self._initialize_sync)
        except (OSError, ValueError) as err:
            self._storage_error(f"{type(err).__name__}: {err}")
        else:
            self._initialized = True
            self._session_gaps = gaps
            self._metrics = metrics
        self._consumer = asyncio.create_task(self._spool(), name="eg4-incident-spool")
        self._notify()

    def observe(self, record: dict[str, Any]) -> None:
        """Buffer received evidence and immediate edges, with no filesystem work."""
        if self._closed:
            return
        now = self._now()
        try:
            encoded = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()
            start, end = _interval(record, now)
        except (TypeError, ValueError, OverflowError, RecursionError) as err:
            self._capture_loss(f"serialization: {type(err).__name__}: {err}", 1, 0)
            return
        item = _Buffered(
            encoded, now, start, end,
            record.get("role") if record.get("role") in self.roles else None,
            record.get("ok") is True and record.get("kind", "acquisition") == "acquisition"
            and record.get("schema") != "eg4_controller_observation.v1",
        )
        self._trim_buffer(now)
        self._buffer.append(item)
        self._buffer_bytes += len(encoded)
        while self._buffer and self._buffer_bytes > self.limits.pre_max_bytes:
            removed = self._buffer.popleft()
            self._buffer_bytes -= len(removed.line)
            self._memory_lost_at = now
        before = self._active
        self._detect(record)
        if self._active is not None and self._active is before:
            self._enqueue(self._active, (item,))
        elif self._active is not None and (not self._buffer or self._buffer[-1] is not item):
            # A trigger larger than the memory prebuffer is still live evidence.
            self._enqueue(self._active, (item,))
        self._notify()

    def _trim_buffer(self, now: datetime) -> None:
        """Apply the time ceiling as well as the independent byte ceiling."""
        while self._buffer and (now - self._buffer[0].received_at).total_seconds() > self.limits.pre_seconds:
            self._buffer_bytes -= len(self._buffer.popleft().line)

    def _pre_coverage(self, now: datetime) -> float:
        """Report buffered receive coverage, not observed timestamps as disk coverage."""
        if not self._buffer:
            return 0.0
        return max(0.0, min(self.limits.pre_seconds, (now - self._buffer[0].received_at).total_seconds()))

    def _mark_incomplete(self, incident: _Incident, reason: str, *, truncated: bool = False) -> None:
        """Accumulate monotonic evidence loss without erasing earlier reasons."""
        incident.manifest["complete"] = False
        incident.manifest["truncated"] |= truncated
        _bounded_add(incident.manifest["incomplete_reasons"], reason)

    def _capture_loss(self, error: str, count: int, size: int) -> None:
        """Make bounded backlog/serialization loss visible and edge-triggered."""
        self._losses += count
        self._lost_bytes += size
        self._last_error = error
        was_lost = self._own_loss
        self._own_loss = True
        self._active_anomalies.add("recorder:incident_persistence")
        affected = self._active or (self._latest if self._closed else None)
        if affected is not None:
            self._mark_incomplete(affected, "incident_spool_loss", truncated=True)
            _bounded_add(affected.manifest["errors"], error)
            affected.manifest["lost_record_count"] = self._losses
            affected.manifest["lost_bytes"] = self._lost_bytes
        if not was_lost and not self._closed:
            self._trigger("recorder_persistence_lost", {"role": "recorder"}, {"error": error})
        self._notify()

    def _storage_error(self, error: str) -> None:
        """Marshal storage faults into current health and an incident loss edge."""
        self._current_error = self._last_error = error
        self._capture_loss(error, 0, 0)

    def _thread_error(self, stream: str, error: str) -> None:
        """Deliver worker faults on the owning loop, never mutate loop state here."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._guard_failed, stream, error)

    def _guard_failed(self, stream: str, error: str) -> None:
        """Keep a stream degraded until an actual subsequent guarded append works."""
        self._guard_errors.add(stream)
        if stream == "incidents":
            self._storage_error(error)
        else:
            # Losing ordinary persistence is an incident trigger, not proof
            # that its independently spooled copy lost the same received bytes.
            self._current_error = self._last_error = error
            self._edge(
                f"recorder:guard:{stream}", True, "recorder_persistence_lost",
                {"role": "recorder"}, {"stream": stream, "error": error},
            )
            self._notify()

    def _guard_recovered(self, stream: str) -> None:
        """Acknowledge worker recovery on the source loop, retaining loss history."""
        self._guard_errors.discard(stream)
        if stream != "incidents":
            self._edge(
                f"recorder:guard:{stream}", False, "recorder_persistence_lost",
                {"role": "recorder"}, {"stream": stream},
            )
        if not self._guard_errors and not self._own_loss:
            self._current_error = None
        self._notify()

    def _context(self) -> dict[str, Any]:
        """Capture configuration provenance without asserting effective-change time."""
        context = self._context_provider() if self._context_provider is not None else {}
        detached = json.loads(json.dumps(context, allow_nan=False))
        if not isinstance(detached, dict):
            raise ValueError("incident context provider must return a dictionary")
        detached.setdefault("configuration_observed_at", _iso(self._now()))
        detached.setdefault("effective_change_time_known", False)
        return detached

    def _trigger(self, reason: str, source: dict[str, Any], details: dict[str, Any]) -> None:
        """Merge every edge into one window; spool individual events, not a RAM log."""
        now = self._now()
        created = self._active is None
        if created:
            incident_id = f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:12]}"
            warmup = self._started_at is None or (now - self._started_at).total_seconds() < self.limits.pre_seconds
            truncated = self._memory_lost_at is not None and (now - self._memory_lost_at).total_seconds() <= self.limits.pre_seconds
            reasons = [reason for reason, present in (("prebuffer_warmup", warmup), ("prebuffer_byte_limit", truncated)) if present]
            manifest = {
                "schema": _INCIDENT_SCHEMA, "incident_id": incident_id,
                "session_id": self.session_id, "build_id": self.build_id,
                "entry_id": self.entry_id, "evidence_class": self.evidence_class,
                "state": "open", "started_at": _iso(now), "last_trigger_at": _iso(now), "closed_at": None,
                "expected_roles": sorted(self.roles), "observed_roles": [], "missing_roles": sorted(self.roles),
                "complete": False, "truncated": truncated, "incomplete_reasons": reasons,
                "errors": [], "gaps": list(self._session_gaps),
                "coverage": {
                    "requested_pre_seconds": self.limits.pre_seconds,
                    "requested_post_seconds": self.limits.post_seconds,
                    "actual_start": None, "actual_end": None,
                    "pre_start": None, "pre_end": None,
                    "warmup": warmup, "memory_truncated": truncated,
                },
                "configuration_context": {}, "triggers": [], "trigger_count": 0,
                "trigger_files": [], "files": [],
                "lost_record_count": self._losses, "lost_bytes": self._lost_bytes,
                "loss_counters_scope": "recorder_session",
            }
            self._active = _Incident(manifest, self.incidents_root / f"incident-{incident_id}", now, now)
            self._latest = self._active
            self._pending[incident_id] = self._active
            if self._own_loss:
                self._mark_incomplete(self._active, "incident_spool_loss", truncated=True)
            self._enqueue(self._active, tuple(self._buffer), pre=True)
        incident = self._active
        if incident is None:
            return
        incident.last_trigger = now
        incident.manifest["last_trigger_at"] = _iso(now)
        incident.manifest["trigger_count"] += 1
        summary_key = f"{source.get('role', '')}:{reason}"
        if summary_key not in incident.reason_summaries and len(incident.reason_summaries) < _MAX_SUMMARIES:
            incident.reason_summaries[summary_key] = {
                "reason": reason, "role": source.get("role"),
                "first_at": _iso(now), "last_at": _iso(now), "count": 0,
            }
        summary = incident.reason_summaries.get(summary_key)
        if summary is not None:
            summary["last_at"], summary["count"] = _iso(now), summary["count"] + 1
        else:
            incident.manifest["additional_trigger_summary_count"] = incident.manifest.get("additional_trigger_summary_count", 0) + 1
        incident.manifest["triggers"] = list(incident.reason_summaries.values())
        event = {
            "schema": "eg4_incident_trigger.v1", "kind": "incident_trigger",
            "incident_id": incident.manifest["incident_id"], "session_id": self.session_id,
            "observed_at": _iso(now), "reason": reason, "role": source.get("role"),
            "details": details,
            **{key: source[key] for key in (
                "acquisition_id", "transaction_id", "transaction_ids", "started_at", "completed_at",
                "sample_started_at", "sample_completed_at", "primary_source",
            ) if key in source},
        }
        self._enqueue_event(incident, event, "triggers")
        try:
            context = self._context()
        except Exception as err:
            self._mark_incomplete(incident, "configuration_context_unavailable")
            _bounded_add(incident.manifest["errors"], f"context: {type(err).__name__}: {err}")
        else:
            if created:
                incident.manifest["configuration_context"] = context
            self._enqueue_event(incident, {
                "schema": "eg4_incident_context.v1", "kind": "incident_context",
                "incident_id": incident.manifest["incident_id"], "session_id": self.session_id,
                "observed_at": _iso(now), **context,
            }, "records")
        self._notify()

    def _enqueue_event(self, incident: _Incident, record: dict[str, Any], kind: str) -> None:
        """Apply the same bounded ordered queue to discrete events and context."""
        now = self._now()
        try:
            line = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()
        except (TypeError, ValueError, OverflowError, RecursionError) as err:
            self._mark_incomplete(incident, "trigger_serialization_loss", truncated=True)
            self._last_error = f"{type(err).__name__}: {err}"
            return
        stamp = _iso(now)
        self._enqueue(incident, (_Buffered(line, now, stamp, stamp, None, False),), kind=kind)

    def _enqueue(
        self, incident: _Incident, records: tuple[_Buffered, ...], *, pre: bool = False,
        kind: str = "records",
    ) -> None:
        """Reserve bounded spool space without waiting or spawning per-record tasks."""
        size = sum(len(item.line) for item in records)
        if self._queued_bytes + size > self.limits.max_queue_bytes or self._queued_records + len(records) > self.limits.max_queue_records:
            self._mark_incomplete(incident, "incident_spool_loss", truncated=True)
            if kind == "triggers":
                incident.manifest["lost_trigger_count"] = incident.manifest.get("lost_trigger_count", 0) + len(records)
            self._capture_loss("incident spool queue limit reached", len(records), size)
            return
        if records:
            self._queued_bytes += size
            self._queued_records += len(records)
            self._work.append(_Work(incident, records, kind, pre))
            self._wake.set()

    def _edge(
        self, key: str, value: bool, reason: str, source: dict[str, Any],
        details: dict[str, Any], *, initial_failure: bool = True,
    ) -> None:
        """Set a monitored active predicate and emit only onset/recovery edges."""
        previous = self._predicates.get(key)
        self._predicates[key] = value
        if value:
            self._active_anomalies.add(key)
        else:
            self._active_anomalies.discard(key)
        if (previous is None and value and initial_failure) or (previous is not None and previous != value):
            self._trigger(reason if value else f"{reason}_recovered", source, details)

    def _availability(self, key: str, value: bool | None, source: dict[str, Any], *, primary: bool = False) -> None:
        """Ignore never-attempted None; a real initial failed attempt is evidence."""
        if value is not None:
            self._edge(key, not value, "primary_availability_lost" if primary else "diagnostic_availability_lost", source, {"channel": key, "available": value})

    def _hysteresis(self, key: str, value: float | None, on: float, off: float) -> bool | None:
        """Reuse existing recorder/logger hysteresis without HA debounce."""
        previous = self._predicates.get(key)
        if value is None:
            return None
        current = True if value >= on else False if value <= off else previous
        if current is not None:
            self._predicates[key] = current
        return current

    def _output(self, role: str, channel: str, value: Any, source: dict[str, Any]) -> None:
        """Apply 180/264-V capture thresholds only to the site's reported 240-V channels."""
        voltage = _number(value)
        key = f"{role}:output:{channel}"
        self._availability(f"{key}:measurement", voltage is not None, source)
        live = bool(self._predicates.get(f"{key}:ever_live"))
        if voltage is None:
            if live:
                self._edge(f"{key}:unavailable", True, "output_voltage_unavailable", source, {"channel": channel, "previously_live": True})
            return
        self._edge(f"{key}:unavailable", False, "output_voltage_unavailable", source, {"channel": channel, "reported_voltage_v": voltage})
        if voltage >= 180:
            self._predicates[f"{key}:ever_live"] = True
        self._edge(key, voltage > 264 or (live and voltage < 180), "output_voltage_anomaly", source, {"channel": channel, "reported_voltage_v": voltage, "previously_live": live, "physical_waveform_measured": False})

    def _analog(self, role: str, values: dict[str, Any], source: dict[str, Any]) -> None:
        """Keep accepted power independent of qualified frequency and voltage presence."""
        if role.startswith("gridboss"):
            if "ups_voltage_v" in values:
                self._output(role, "ups_voltage_v", values["ups_voltage_v"], source)
            pairs = (("generator", "generator_voltage_v", "generator_frequency_hz"), ("grid", "grid_voltage_v", "grid_frequency_hz"))
        else:
            if "eps_voltage_r_v" in values:
                self._output(role, "eps_voltage_r_v", values["eps_voltage_r_v"], source)
            pairs = (("eps", "eps_voltage_r_v", "eps_frequency_hz"), ("grid", "grid_voltage_v", "grid_frequency_hz"))
        for name, voltage_key, frequency_key in pairs:
            if voltage_key not in values and frequency_key not in values:
                continue
            key = f"{role}:vf:{name}"
            # A partial power block must not manufacture a frequency failure.
            for field_name in (voltage_key, frequency_key):
                if field_name in values:
                    value = _number(values[field_name])
                    self._availability(f"{key}:{field_name}:measurement", value is not None, source)
                    self._predicates[f"{key}:{field_name}:value"] = value
                    self._predicates[f"{key}:{field_name}:source"] = deepcopy({
                        name: source[name] for name in (
                            "acquisition_id", "transaction_id", "started_at", "completed_at",
                            "sample_started_at", "sample_completed_at", "primary_source",
                            "raw_values_semantically_validated",
                        ) if name in source
                    })
            voltage = self._predicates.get(f"{key}:{voltage_key}:value")
            frequency = self._predicates.get(f"{key}:{frequency_key}:value")
            channel_sources = {
                field_name: self._predicates.get(f"{key}:{field_name}:source")
                for field_name in (voltage_key, frequency_key)
            }
            present = self._hysteresis(f"{key}:present", voltage, GENERATOR_ON_VOLTAGE, GENERATOR_OFF_VOLTAGE)
            qualified = self._hysteresis(f"{key}:qualified", frequency, GENERATOR_ON_FREQUENCY, GENERATOR_OFF_FREQUENCY)
            if voltage is not None and frequency is not None and present is not None and qualified is not None:
                self._edge(f"{key}:inconsistent", present != qualified, "voltage_frequency_inconsistent", source, {"source": name, "reported_voltage_v": voltage, "qualified_frequency_hz": frequency, "mechanical_state": "unmeasured", "source_intervals": channel_sources, "causal_ordering": "unresolved"})
            if name == "generator":
                if present:
                    self._predicates[f"{key}:ever_present"] = True
                if present is not None and self._predicates.get(f"{key}:ever_present"):
                    self._edge(f"{key}:source_unavailable", not present, "generator_voltage_unavailable", source, {"reported_voltage_v": voltage, "mechanical_state": "unmeasured"})
                if qualified is not None and present is not None:
                    self._edge(
                        f"{key}:qualification", present and not qualified,
                        "generator_qualification_unavailable", source,
                        {"reported_voltage_v": voltage, "qualified_frequency_hz": frequency,
                         "mechanical_state": "unmeasured"},
                    )
                if "generator_power_w" in values:
                    power = _number(values["generator_power_w"])
                    self._availability(f"{role}:generator:accepted_power_measurement", power is not None, source)
                    was_supplying = self._predicates.get(f"{role}:generator:supplying")
                    magnitude = abs(power) if power is not None else None
                    supplying = self._hysteresis(f"{role}:generator:supplying", magnitude, GENERATOR_ON_POWER, GENERATOR_OFF_POWER)
                    if was_supplying is True and supplying is False and present:
                        self._edge(f"{role}:accepted_power_lost", True, "accepted_generator_power_lost", source, {"reported_voltage_v": voltage, "power_w": power, "qualified_frequency_hz": frequency, "source_intervals": channel_sources, "mechanical_state": "unmeasured"})
                    elif supplying is True or present is False:
                        self._edge(f"{role}:accepted_power_lost", False, "accepted_generator_power_lost", source, {"power_w": power, "reported_voltage_v": voltage})

    def _raw_values(
        self, role: str, block: dict[str, Any], source: dict[str, Any],
    ) -> dict[str, float | None]:
        """Project received lanes separately from decoded publication usability."""
        words, start = block.get("values"), block.get("register")
        if (
            block.get("function") != 4 or not isinstance(words, (list, tuple))
            or type(start) is not int
        ):
            return {}
        values = {
            name: value for name, value in raw_primary_measurements(
                words, register=start, gridboss=role.startswith("gridboss"),
            ).items() if name in _MONITORED_MEASUREMENTS
        }
        if values:
            self._analog(role, values, {**source, "raw_values_semantically_validated": False})
        return values

    def _detect(self, record: dict[str, Any]) -> None:
        """Evaluate only received attempts/settled verdicts, never a second reader."""
        role = record.get("role")
        kind = record.get("kind", "acquisition")
        if kind == "recorder_health":
            health = record.get("health") or {}
            for name in ("RawPersistence", "JournalPersistence"):
                state = health.get(name) or {}
                if state.get("State") in ("healthy", "degraded"):
                    self._edge(f"recorder:{name}", state["State"] == "degraded", "recorder_persistence_lost", record, {"stream": name, **state})
            if record.get("reason") != "shutdown":
                for name, state in (health.get("Roles") or {}).items():
                    if state.get("LastAttempt") is not None:
                        self._availability(f"{name}:primary", state.get("PrimaryAvailable"), {**record, "role": name}, primary=True)
            return
        if role not in self.roles:
            return
        if kind == "transaction":
            request = record.get("request") or {}
            shape = f"{request.get('function')}:{request.get('register')}:{request.get('count')}"
            transaction_id = record.get("transaction_id")
            if transaction_id:
                self._transactions[transaction_id] = f"{role}:{shape}"
                while len(self._transactions) > 1024:
                    self._transactions.popitem(last=False)
            if record.get("ok") is not None:
                self._availability(f"{role}:block:{shape}", record.get("ok") is True, record, primary=shape in (("4:0:40", "4:120:12") if role.startswith("gridboss") else ("4:0:40",)))
            for frame in record.get("frames") or ():
                if frame.get("classification") in ("matching", "matched") or frame.get("matched") is True:
                    self._raw_values(role, {**request, "values": frame.get("values")}, record)
            return
        if kind == "interpretation":
            verdict = record.get("semantic")
            if verdict in ("accepted", "rejected"):
                key = self._transactions.get(record.get("transaction_id") or "", f"{role}:primary")
                self._edge(f"semantic:{key}", verdict == "rejected", "semantic_rejection", record, {"semantic": verdict, "error": record.get("error")})
            return
        if record.get("error", "") and "CancelledError" in str(record["error"]):
            return
        observation = record.get("schema") == "eg4_controller_observation.v1"
        availability = record.get("availability") or {}
        self._availability(f"{role}:primary", availability.get("primary") if observation else record.get("ok"), record, primary=True)
        if observation:
            for shape, value in (availability.get("diagnostics") or {}).items():
                self._availability(f"{role}:block:{shape}", value, record)
            changed = []
            controller = record.get("controller") or {}
            for field_name in record.get("changed_fields") or ():
                if field_name.startswith("availability."):
                    continue
                parts = field_name.split(".")
                value = None
                if len(parts) >= 2 and parts[0] in ("input", "holding"):
                    value = (controller.get(parts[0]) or {}).get(parts[1])
                    if field_name == "input.6.bits_0_1" and isinstance(value, int):
                        value &= 3
                elif field_name.startswith("transfer."):
                    value = (record.get("observed_transfer") or {}).get(field_name[9:])
                settled_key = f"controller:{role}:{field_name}"
                previous = self._predicates.get(settled_key)
                if value is not None:
                    self._predicates[settled_key] = value
                    if previous is not None and previous != value:
                        changed.append(field_name)
            # First baselines establish settled values without turning staggered
            # first diagnostic attempts into controller-change storms.
            if record.get("observation") == "baseline":
                for family in ("input", "holding"):
                    for index, value in (controller.get(family) or {}).items():
                        name = f"{family}.{index}"
                        if family == "input" and index == "6" and not role.startswith("gridboss"):
                            name, value = "input.6.bits_0_1", value & 3 if isinstance(value, int) else None
                        if value is not None:
                            self._predicates[f"controller:{role}:{name}"] = value
                for name, value in (record.get("observed_transfer") or {}).items():
                    if value is not None:
                        self._predicates[f"controller:{role}:transfer.{name}"] = value
            if changed:
                self._trigger("controller_changed", record, {"changed_fields": changed, "controller": controller, "observed_transfer": record.get("observed_transfer"), "installed_semantics": "unverified"})
            if not role.startswith("gridboss"):
                words = controller.get("input") or {}
                for index in (6, 60, 61, 62, 63):
                    word = words.get(str(index))
                    if isinstance(word, int):
                        value = word & 3 if index == 6 else word
                        self._edge(f"{role}:protection:{index}", value != 0, "controller_protection_word_nonzero", record, {"register": index, "raw_word": word, "trigger_value": value, "physical_meaning": "unknown"})
        raw_fields: set[str] = set()
        for block in (*record.get("primary_blocks", ()), *record.get("diagnostics", ())):
            shape = f"{block.get('function')}:{block.get('register')}:{block.get('count')}"
            self._availability(f"{role}:block:{shape}", block.get("ok"), record, primary=block in record.get("primary_blocks", ()))
            verdict = block.get("semantic")
            if verdict in ("accepted", "rejected"):
                self._edge(f"semantic:{role}:{shape}", verdict == "rejected", "semantic_rejection", record, {"semantic": verdict, "transaction_id": block.get("transaction_id"), "error": block.get("semantic_error")})
            raw_fields.update(self._raw_values(role, block, record))
        telemetry = record.get("telemetry") or {}
        values = telemetry.get("decoded" if role.startswith("gridboss") else "core")
        if isinstance(values, dict):
            fallback = {}
            for name, value in values.items():
                if name in raw_fields:
                    self._availability(
                        f"{role}:decoded:{name}:measurement", _number(value) is not None, record,
                    )
                else:
                    fallback[name] = value
            self._analog(role, fallback, record)

    async def _spool(self) -> None:
        """Serialize all pre/live appends and barriers through the existing writer."""
        while True:
            await self._wake.wait()
            if not self._work:
                self._wake.clear()
                continue
            work = self._work.popleft()
            try:
                if work.kind == "stop":
                    if work.done is not None:
                        work.done.set_result(None)
                    return
                if work.kind in ("checkpoint", "finalize"):
                    await self._checkpoint(work.incident if work.kind == "finalize" else None)
                elif work.incident is not None:
                    await self._append(work)
            except (OSError, ValueError) as err:
                self._storage_error(f"{type(err).__name__}: {err}")
                if work.records:
                    self._capture_loss("incident batch persistence failed", len(work.records), sum(len(item.line) for item in work.records))
                if work.incident is not None:
                    self._mark_incomplete(work.incident, "incident_write_failure", truncated=True)
                    _bounded_add(work.incident.manifest["errors"], self._last_error)
            finally:
                if work.kind == "finalize" and work.incident is not None:
                    for writer in work.incident.writers.values():
                        await writer.async_close()
                    await asyncio.to_thread(self._release_incident_sync, work.incident.path)
                    self._pending.pop(work.incident.manifest["incident_id"], None)
                self._queued_bytes -= sum(len(item.line) for item in work.records)
                self._queued_records -= len(work.records)
                if work.done is not None and not work.done.done():
                    work.done.set_result(None)
                if not self._work:
                    self._wake.clear()

    async def _append(self, work: _Work) -> None:
        """Drain a bounded prebuffer before live continuation, recording actual saves."""
        incident = work.incident
        if incident is None:
            return
        if not incident.writers:
            await asyncio.to_thread(self._prepare_incident_sync, self._snapshot(incident), incident.path)
        writer = incident.writers.get(work.kind)
        if writer is None:
            writer = LocalDongleTraceWriter(
                incident.path / work.kind, max_bytes=self.limits.segment_bytes,
                max_files=None, session_id=self.session_id,
                max_queue_bytes=max(self.limits.max_queue_bytes, self.limits.pre_max_bytes),
                write_guard=self.write_guard("incidents"),
            )
            incident.writers[work.kind] = writer
        for item in work.records:
            record = json.loads(item.line)
            record.setdefault("incident_id", incident.manifest["incident_id"])
            record.setdefault("build_id", self.build_id)
            record.setdefault("evidence_class", self.evidence_class)
            record.setdefault("incident_capture", {
                "pretrigger": work.pre, "received_at": _iso(item.received_at),
            })
            path = await writer.async_write(record)
            if path is None:
                self._storage_error(writer.health["LastError"] or "incident append rejected")
                self._capture_loss("incident record was not persisted", 1, len(item.line))
                self._mark_incomplete(incident, "incident_write_failure", truncated=True)
                continue
            if work.kind == "records" and record.get("kind") != "incident_context":
                coverage = incident.manifest.setdefault("original_coverage", deepcopy(incident.manifest["coverage"]))
                for name, value, earliest in (("actual_start", item.start, True), ("actual_end", item.end, False)):
                    previous = coverage[name]
                    coverage[name] = value if previous is None else (min(previous, value) if earliest else max(previous, value))
                if work.pre:
                    coverage["pre_start"] = min(coverage["pre_start"] or item.start, item.start)
                    coverage["pre_end"] = max(coverage["pre_end"] or item.end, item.end)
                if item.role:
                    per_role = incident.coverage_by_role.setdefault(item.role, {"start": item.start, "end": item.end})
                    per_role["start"], per_role["end"] = min(per_role["start"], item.start), max(per_role["end"], item.end)
                    if item.usable_primary:
                        incident.observed_roles.add(item.role)

    def _snapshot(self, incident: _Incident) -> dict[str, Any]:
        """Detach a bounded manifest on the source loop before filesystem work."""
        manifest = incident.manifest
        manifest["original_observed_roles"] = sorted(set(manifest.get("original_observed_roles", ())) | incident.observed_roles)
        original = manifest.setdefault("original_coverage", deepcopy(manifest["coverage"]))
        self._merge_coverage(original, {"by_role": incident.coverage_by_role})
        return json.loads(json.dumps(manifest, allow_nan=False))

    async def _checkpoint(self, finalized: _Incident | None) -> None:
        """Drain/synchronize owned writers, publish manifests and cached disk metrics."""
        if not self._initialized:
            gaps, self._metrics = await asyncio.to_thread(self._initialize_sync)
            self._session_gaps = gaps
            self._initialized = True
        targets = [finalized] if finalized is not None else list(self._pending.values())
        successful = True
        for incident in targets:
            synchronized = True
            for writer in incident.writers.values():
                synchronized = await writer.async_sync() and synchronized
                if finalized is not None:
                    await writer.async_close()
                    synchronized = await writer.async_sync() and synchronized
            if not synchronized:
                successful = False
                self._mark_incomplete(incident, "incident_synchronization_failed")
                self._storage_error("incident synchronization failed")
            if finalized is not None:
                incident.manifest["complete"] = False
            saved, metrics = await asyncio.to_thread(
                self._checkpoint_sync, self._snapshot(incident), incident.path,
                finalized is not None and synchronized,
            )
            # Source-loop observations may have arrived while the filesystem
            # snapshot was saved. Merge only custody/loss fields, never replace
            # their newer trigger count, receive window or actual append coverage.
            self._apply_saved_custody(incident, saved)
            self._metrics = metrics
            if finalized is not None:
                self._pending.pop(incident.manifest["incident_id"], None)
        open_ids = tuple(item.manifest["incident_id"] for item in self._pending.values() if item.manifest["state"] == "open")
        self._metrics = await asyncio.to_thread(self._retention_metrics_sync, open_ids)
        latest = self._latest
        if latest is not None:
            latest_saved = await asyncio.to_thread(self._read_incident_manifest_sync, latest.path)
            if self._latest is latest:
                if latest_saved is not None:
                    self._apply_saved_custody(latest, latest_saved)
                elif latest is not self._active and latest.manifest["state"] != "open":
                    self._latest = None
        if successful and not self._guard_errors:
            self._current_error = None
            if self._own_loss:
                self._own_loss = False
                self._active_anomalies.discard("recorder:incident_persistence")
                if not self._closed:
                    self._trigger("recorder_persistence_recovered", {"role": "recorder"}, {"stream": "incidents", "cumulative_dropped_records": self._losses})
        self._notify()

    def _apply_saved_custody(self, incident: _Incident, saved: dict[str, Any]) -> None:
        """Refresh retained context without replacing newer source-loop trigger state."""
        incident.manifest["files"] = saved["files"]
        incident.manifest["trigger_files"] = saved["trigger_files"]
        self._merge_losses(incident.manifest, saved)
        incident.manifest["coverage"] = saved["coverage"]
        incident.manifest["observed_roles"] = saved["observed_roles"]
        incident.manifest["missing_roles"] = saved["missing_roles"]
        incident.manifest["complete"] = bool(saved["complete"]) and not (
            incident.manifest["incomplete_reasons"] or incident.manifest["truncated"]
            or incident.manifest["missing_roles"]
        )

    def _read_incident_manifest_sync(self, directory: Path) -> dict[str, Any] | None:
        """Read a current owned manifest or observe its completed age retirement."""
        with self._fs_lock:
            path = directory / "manifest.json"
            try:
                document = self._load_manifest_sync(path, mutable=True)
            except FileNotFoundError:
                return None
            return document

    async def _barrier(self, kind: str, incident: _Incident | None = None) -> None:
        """Await an ordered spool barrier, not a continuously growing live queue."""
        future = asyncio.get_running_loop().create_future()
        self._work.append(_Work(incident, kind=kind, done=future))
        self._wake.set()
        await asyncio.shield(future)

    async def async_tick(self) -> None:
        """Finalize quiet windows and sample actual retained storage off-loop."""
        if self._consumer is None or self._closed:
            return
        async with self._tick_lock:
            now = self._now()
            self._trim_buffer(now)
            incident = self._active
            if incident is not None and not self._active_anomalies and (now - incident.last_trigger).total_seconds() >= self.limits.post_seconds:
                self._active = None
                incident.manifest["state"] = "closed"
                incident.manifest["closed_at"] = _iso(now)
                await self._barrier("finalize", incident)
            else:
                await self._barrier("checkpoint")
            self._notify()

    async def async_close(self) -> None:
        """Drain and explicitly interrupt unfinished windows, even across cancellation."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        """Own shutdown through the final writer receipt and directory synchronization."""
        if self._consumer is None and self._work:
            await self.async_start()
        self._closed = True
        self._context_provider = None
        if self._consumer is None:
            self._buffer.clear()
            self._buffer_bytes = 0
            return
        async with self._tick_lock:
            incident = self._active
            self._active = None
            if incident is not None:
                incident.manifest["state"] = "interrupted"
                incident.manifest["closed_at"] = _iso(self._now())
                self._mark_incomplete(incident, "shutdown_before_window_complete")
                _bounded_add(incident.manifest["gaps"], {"reason": "recorder_shutdown", "observed_at": _iso(self._now()), "active_anomalies": sorted(self._active_anomalies)})
                await self._barrier("finalize", incident)
            else:
                # A cancelled health caller may already have detached its quiet
                # window. Its shielded spool barrier still owns those appends;
                # drain it before closing any remaining writer.
                await self._barrier("checkpoint")
            for pending in tuple(self._pending.values()):
                for writer in pending.writers.values():
                    await writer.async_close()
            await self._barrier("stop")
            await self._consumer
            self._buffer.clear()
            self._buffer_bytes = 0
            self._health_callback = None

    def write_guard(self, stream: str) -> Callable[[Path, int], AbstractContextManager[None]]:
        """Return a native-thread quota/reserve guard; never acquire it on the loop."""
        if stream not in ("raw", "journal", "incidents"):
            raise ValueError(f"unknown recorder stream: {stream}")

        @contextmanager
        def guard(path: Path, size: int) -> Iterator[None]:
            """Serialize ownership checks, pruning and the actual append/rotation."""
            new_target = False
            try:
                with self._fs_lock:
                    self._assert_owner_sync()
                    self._assert_path_sync(path, stream)
                    self._recover_pending_unlinks_sync()
                    if path.exists():
                        info = path.stat()
                        if (self._recognized_segments.get(path) != (info.st_dev, info.st_ino)
                                and path not in self._segments_sync(path.parent)):
                            raise OSError("refusing to append to unrecognized recorder evidence")
                    # Leave transient room for the atomic ownership commit as
                    # well as the append; its bytes are not a stream payload.
                    marker_bytes = (self.root / "recorder.json").stat().st_size
                    self._enforce_space_sync(stream, path, size, reserve_bytes=max(size, marker_bytes + 1024))
                    self._prepare_owned_active_sync(path)
                    self._enforce_space_sync(stream, path, size)
                    if not path.exists():
                        self._prepare_owned_active_sync(path)
                    new_target = not path.exists()
                    yield
                    if path.exists() and _SEGMENT.fullmatch(path.name):
                        info = path.stat()
                        if self._recognized_segments.get(path) != (info.st_dev, info.st_ino):
                            self._segment_cache.pop(path, None)
                        self._recognized_segments[path] = (info.st_dev, info.st_ino)
                    if stream in self._worker_failed_streams and path.exists() and path.stat().st_size > 0:
                        self._worker_failed_streams.remove(stream)
                        if self._loop is not None and not self._loop.is_closed():
                            self._loop.call_soon_threadsafe(self._guard_recovered, stream)
            except (OSError, ValueError) as err:
                with self._fs_lock:
                    if new_target and path.exists():
                        self._assert_path_sync(path, stream)
                        info = path.stat()
                        self._recognized_segments[path] = (info.st_dev, info.st_ino)
                    self._worker_failed_streams.add(stream)
                self._thread_error(stream, f"{type(err).__name__}: {err}")
                if isinstance(err, OSError):
                    raise
                raise OSError(f"invalid recorder storage metadata: {err}") from err

        return guard

    def _assert_path_sync(self, path: Path, stream: str | None = None) -> None:
        """Reject symlinks, traversal and paths outside the precise owned stream."""
        path = Path(path).absolute()
        base = {"raw": self.raw_root, "journal": self.journal_root, "incidents": self.incidents_root}.get(stream or "", self.root)
        try:
            path.relative_to(base)
        except ValueError as err:
            raise OSError("recorder path escapes owned storage") from err
        if ".." in path.parts:
            raise OSError("recorder traversal is not allowed")
        for candidate in (path, *path.parents):
            try:
                mode = candidate.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise OSError(f"recorder symlink rejected: {candidate}")

    def _assert_owner_sync(self) -> None:
        """Require a regular matching ownership marker before every guarded write."""
        self._assert_path_sync(self.root)
        marker = self.root / "recorder.json"
        self._assert_path_sync(marker)
        info = marker.stat()
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if self._owner_cache is not None and self._owner_signature == signature:
            return
        try:
            with marker.open(encoding="utf-8") as source:
                owner = json.load(source)
        except ValueError as err:
            raise OSError("invalid recorder ownership marker") from err
        if not isinstance(owner, dict) or owner.get("schema") != _RECORDER_SCHEMA or owner.get("entry_id") != self.entry_id:
            raise OSError("recorder root belongs to a different entry or schema")
        self._owner_cache, self._owner_signature = owner, signature

    def _owned_files_sync(self, *, mutable: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read durable inode claims without treating names as proof of ownership."""
        self._assert_owner_sync()
        cached = self._owner_cache
        if cached is None:
            raise OSError("recorder ownership marker is unavailable")
        owner = deepcopy(cached) if mutable else cached
        claims = owner.setdefault("owned_files", {}) if mutable else owner.get("owned_files", {})
        if not isinstance(claims, dict):
            raise OSError("invalid recorder file ownership registry")
        return owner, claims

    def _claim_matches_sync(self, path: Path, claim: Any) -> bool:
        """Match an exact regular inode at an already validated owned-root path."""
        self._assert_path_sync(path)
        if not isinstance(claim, dict):
            return False
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISREG(info.st_mode) and (
            claim.get("device"), claim.get("inode")
        ) == (info.st_dev, info.st_ino)

    def _recover_claim_stage_sync(
        self, path: Path, claim: dict[str, Any],
    ) -> None:
        """Finish an owned empty-file publication interrupted before its first append."""
        name = claim.get("stage")
        if not isinstance(name, str) or not re.fullmatch(r"\.local-dongle-[0-9a-f]{32}\.pending", name):
            return
        staging = path.parent / name
        if not self._claim_matches_sync(staging, claim):
            return
        if not path.exists():
            os.link(staging, path, follow_symlinks=False)
        if self._claim_matches_sync(path, claim):
            staging.unlink()
            self._sync_directory_sync(path.parent)

    def _prepare_owned_active_sync(self, path: Path) -> None:
        """Publish a durably claimed inode, preserving old sessions and broken tails."""
        self._assert_path_sync(path)
        owner, claims = self._owned_files_sync()
        relative = path.relative_to(self.root).as_posix()
        value = claims.get(relative)
        claim: dict[str, Any] = value if isinstance(value, dict) else {}
        if claim:
            self._recover_claim_stage_sync(path, claim)
        if path.exists():
            claimed = self._claim_matches_sync(path, claim)
            if not claimed and path not in self._segments_sync(path.parent):
                raise OSError("refusing to append to unrecognized recorder evidence")
            info = path.stat()
            with path.open("rb") as source:
                broken = info.st_size > 0 and os.pread(source.fileno(), 1, info.st_size - 1) != b"\n"
            previous_session = claimed and claim.get("session_id") != self.session_id
            if broken or previous_session:
                # Declare the preserved destination before moving the inode. A
                # crash on either side of rename therefore retains its ownership.
                owner, claims = self._owned_files_sync(mutable=True)
                preserved_name = claim.get("preserving")
                if not isinstance(preserved_name, str) or not _SEGMENT.fullmatch(preserved_name) or preserved_name == path.name:
                    preserved_name = f"local-dongle-{time.time_ns()}.jsonl"
                preserved = path.with_name(preserved_name)
                identity = {
                    "device": info.st_dev, "inode": info.st_ino,
                    "session_id": claim.get("session_id") if claimed else None,
                }
                claims[relative] = {**identity, "preserving": preserved_name}
                claims[preserved.relative_to(self.root).as_posix()] = identity
                self._atomic_json_sync(self.root / "recorder.json", owner)
                with path.open("rb") as source:
                    os.fsync(source.fileno())
                if preserved.exists():
                    if not self._claim_matches_sync(preserved, identity):
                        raise OSError("refusing to replace unrelated preserved evidence")
                    path.unlink()
                else:
                    path.replace(preserved)
                self._sync_directory_sync(path.parent)
                self._recognized_segments.pop(path, None)
                self._segment_cache.pop(path, None)
            else:
                if not claimed:
                    owner, claims = self._owned_files_sync(mutable=True)
                    claims[relative] = {
                        "device": info.st_dev, "inode": info.st_ino,
                        "session_id": self.session_id,
                    }
                    self._atomic_json_sync(self.root / "recorder.json", owner)
                return
        owner, claims = self._owned_files_sync(mutable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".local-dongle-{uuid4().hex}.pending")
        created = False
        try:
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            try:
                info = os.fstat(descriptor)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._sync_directory_sync(path.parent)
            claims[relative] = {
                "device": info.st_dev, "inode": info.st_ino, "stage": staging.name,
                "session_id": self.session_id,
            }
            # The ownership commit precedes publication and every evidence byte.
            self._atomic_json_sync(self.root / "recorder.json", owner)
            os.link(staging, path, follow_symlinks=False)
            staging.unlink()
            self._sync_directory_sync(path.parent)
        except OSError:
            if created and staging.exists():
                staging.unlink()
            raise

    def _forget_owned_file_sync(self, path: Path) -> None:
        """Commit removal intent without turning a crash-left partial file unowned."""
        owner, claims = self._owned_files_sync(mutable=True)
        relative = path.relative_to(self.root).as_posix()
        if relative in claims:
            owner.setdefault("pending_unlinks", {})[relative] = claims.pop(relative)
            self._atomic_json_sync(self.root / "recorder.json", owner, reclamation=True)

    def _complete_owned_unlink_sync(self, path: Path) -> None:
        """Discard a removal intent after its directory entry is synchronized."""
        owner, _ = self._owned_files_sync(mutable=True)
        pending = owner.get("pending_unlinks", {})
        if not isinstance(pending, dict):
            raise OSError("invalid recorder removal registry")
        relative = path.relative_to(self.root).as_posix()
        if relative in pending:
            del pending[relative]
            self._atomic_json_sync(self.root / "recorder.json", owner, reclamation=True)

    def _recover_pending_unlinks_sync(self) -> None:
        """Finish exact owned-inode removals, never append to a deletion-intent file."""
        owner, _ = self._owned_files_sync()
        pending = owner.get("pending_unlinks", {})
        if not isinstance(pending, dict):
            raise OSError("invalid recorder removal registry")
        if not pending:
            return
        owner = deepcopy(owner)
        pending = owner["pending_unlinks"]
        for relative, claim in tuple(pending.items()):
            path = self.root / relative
            self._assert_path_sync(path)
            if not _SEGMENT.fullmatch(path.name):
                raise OSError("invalid recorder removal path")
            if self._claim_matches_sync(path, claim):
                info = path.stat()
                path.unlink()
                self._segment_cache.pop(path, None)
                self._recognized_segments.pop(path, None)
                self._incident_segment_cache.pop((info.st_dev, info.st_ino), None)
                self._sync_directory_sync(path.parent)
            del pending[relative]
        self._atomic_json_sync(self.root / "recorder.json", owner, reclamation=True)

    def _atomic_json_sync(
        self, path: Path, document: dict[str, Any], *, synchronize: bool = True,
        reclamation: bool = False,
    ) -> None:
        """Atomically replace JSON; only bounded reclamation commits may use reserve."""
        self._assert_path_sync(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, separators=(",", ":"), allow_nan=False) + "\n").encode()
        free = shutil.disk_usage(self.root).free
        if free - len(payload) < self.limits.min_free_bytes:
            # Deletion intent/loss must be durable before reclaiming evidence.
            # Allow only these call sites a bounded temporary metadata copy,
            # never an ordinary ownership/manifest update or a payload append.
            if not reclamation or len(payload) > _MAX_RECLAMATION_METADATA_BYTES or free < len(payload):
                raise OSError("recorder free-space reserve prevents manifest write")
        temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(payload)
                destination.flush()
                if synchronize:
                    os.fsync(destination.fileno())
            temporary.replace(path)
            if synchronize:
                self._sync_directory_sync(path.parent)
            if path == self.root / "recorder.json":
                info = path.stat()
                self._owner_cache = deepcopy(document)
                self._owner_signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _sync_directory_sync(self, directory: Path) -> None:
        """Synchronize directory entries without following a symlink directory."""
        self._assert_path_sync(directory)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _initialize_sync(self) -> tuple[list[Any], dict[str, Any]]:
        """Create the narrow ownership root and label crash-interrupted bundles."""
        with self._fs_lock:
            self._assert_path_sync(self.root)
            if self.root.exists() and not (self.root / "recorder.json").exists() and any(self.root.iterdir()):
                raise OSError("nonempty recorder root has no ownership marker")
            self.root.mkdir(parents=True, exist_ok=True)
            marker = self.root / "recorder.json"
            self._assert_path_sync(marker)
            if marker.exists():
                self._assert_owner_sync()
            else:
                self._atomic_json_sync(marker, {
                    "schema": _RECORDER_SCHEMA, "entry_id": self.entry_id,
                    "created_at": _iso(self._now()), "evidence_class": self.evidence_class,
                })
            for directory in (self.raw_root, self.journal_root, self.incidents_root):
                self._assert_path_sync(directory)
                directory.mkdir(exist_ok=True)
            self._recover_pending_unlinks_sync()
            _, claims = self._owned_files_sync()
            for relative, claim in claims.items():
                if isinstance(relative, str) and isinstance(claim, dict):
                    path = self.root / relative
                    self._assert_path_sync(path)
                    if _SEGMENT.fullmatch(path.name):
                        self._recover_claim_stage_sync(path, claim)
            gaps: list[Any] = [{"reason": "new_recorder_session", "session_id": self.session_id, "observed_at": _iso(self._now()), "prebuffer_warmup": True}]
            for path, manifest in self._manifests_sync():
                if manifest.get("state") == "open":
                    manifest = deepcopy(manifest)
                    manifest["state"] = "interrupted"
                    manifest["closed_at"] = _iso(self._now())
                    manifest["complete"] = False
                    _bounded_add(manifest.setdefault("incomplete_reasons", []), "recorder_restart")
                    gap = {"reason": "recorder_restart", "previous_session_id": manifest.get("session_id"), "new_session_id": self.session_id, "observed_at": _iso(self._now()), "tail_continuity_known": False}
                    _bounded_add(manifest.setdefault("gaps", []), gap)
                    _bounded_add(gaps, gap)
                    for item in manifest.get("files", ()):
                        if not item.get("finalized"):
                            item.pop("sha256", None)
                    self._recover_bundle_sync(path.parent, manifest)
                    self._atomic_json_sync(path, manifest)
            self._sync_directory_sync(self.root)
            return gaps, self._retention_metrics_sync()

    def _recover_bundle_sync(self, directory: Path, manifest: dict[str, Any]) -> None:
        """Recover actual pre-checkpoint files after a crash, never a complete tail."""
        self._merge_original_context(manifest, manifest["coverage"], manifest.get("observed_roles", ()))
        entries = {
            item["path"]: item for item in manifest.get("files", ())
            if not item.get("retained", True)
        }
        coverage = manifest.setdefault("coverage", {})
        observed: set[str] = set()
        expected = set(manifest.get("expected_roles", self.roles))
        per_role: dict[str, dict[str, str]] = {}
        trigger_count = 0
        summaries: dict[str, dict[str, Any]] = {}
        for kind in ("records", "triggers"):
            for segment in self._segments_sync(directory / kind):
                relative = segment.relative_to(directory).as_posix()
                if relative in entries:
                    continue
                entries[relative] = {
                    "path": relative, "kind": kind, "bytes": segment.stat().st_size,
                    "retained": True, "finalized": False,
                }
                with segment.open("rb") as source:
                    for line in source:
                        try:
                            record = json.loads(line)
                            if not isinstance(record, dict):
                                raise ValueError("non-object incident JSONL record")
                        except (ValueError, TypeError):
                            _bounded_add(manifest.setdefault("incomplete_reasons", []), "crash_partial_jsonl")
                            continue
                        if kind == "triggers":
                            trigger_count += 1
                            reason = str(record.get("reason", "unknown"))
                            stamp = record.get("observed_at")
                            if reason not in summaries and len(summaries) < _MAX_SUMMARIES:
                                summaries[reason] = {"reason": reason, "first_at": stamp, "last_at": stamp, "count": 0}
                            summary = summaries.get(reason)
                            if summary is not None:
                                summary["count"] += 1
                                if isinstance(stamp, str):
                                    summary["first_at"] = min(summary["first_at"] or stamp, stamp)
                                    summary["last_at"] = max(summary["last_at"] or stamp, stamp)
                            continue
                        if record.get("kind") == "incident_context":
                            continue
                        start, end = _interval(record, self._now())
                        coverage["actual_start"] = min(coverage.get("actual_start") or start, start)
                        coverage["actual_end"] = max(coverage.get("actual_end") or end, end)
                        if (record.get("incident_capture") or {}).get("pretrigger"):
                            coverage["pre_start"] = min(coverage.get("pre_start") or start, start)
                            coverage["pre_end"] = max(coverage.get("pre_end") or end, end)
                        role = record.get("role")
                        if isinstance(role, str) and role in expected:
                            item = per_role.setdefault(role, {"start": start, "end": end})
                            item["start"], item["end"] = min(item["start"], start), max(item["end"], end)
                            if record.get("kind", "acquisition") == "acquisition" and record.get("ok") is True and record.get("schema") != "eg4_controller_observation.v1":
                                observed.add(role)
        manifest["files"] = list(entries.values())
        manifest["trigger_files"] = [item["path"] for item in entries.values() if item["kind"] == "triggers" and item.get("retained")]
        manifest["trigger_count"] = max(manifest.get("trigger_count", 0), trigger_count)
        if not manifest.get("triggers"):
            manifest["triggers"] = list(summaries.values())
        manifest["observed_roles"] = sorted(observed)
        manifest["missing_roles"] = sorted(expected - observed)
        coverage["by_role"] = per_role
        self._refresh_retained_context_sync(manifest, directory)

    def _load_manifest_sync(self, path: Path, *, mutable: bool = False) -> dict[str, Any]:
        """Reuse only unchanged regular owned manifests; copy before mutation."""
        self._assert_path_sync(path, "incidents")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise OSError("incident manifest is not a regular file")
        signature = (
            info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
        )
        cached = self._manifest_cache.get(path)
        if cached is not None and cached[0] == signature:
            document = cached[1]
        else:
            with path.open(encoding="utf-8") as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino) != signature[:2]:
                    raise OSError("incident manifest changed while opening")
                document = json.load(source)
                opened = os.fstat(source.fileno())
                if (
                    opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns, opened.st_ctime_ns,
                ) != signature:
                    raise OSError("incident manifest changed while reading")
            if not isinstance(document, dict) or document.get("schema") != _INCIDENT_SCHEMA or document.get("entry_id") != self.entry_id:
                raise OSError("invalid owned incident manifest")
            self._manifest_cache[path] = (signature, document)
        return deepcopy(document) if mutable else document

    def _manifests_sync(self) -> list[tuple[Path, dict[str, Any]]]:
        """Discover owned manifests as read-only cached documents under the lock."""
        result = []
        if not self.incidents_root.exists():
            return result
        self._assert_path_sync(self.incidents_root)
        for directory in self.incidents_root.iterdir():
            if not _INCIDENT_DIR.fullmatch(directory.name):
                continue
            try:
                path = directory / "manifest.json"
                manifest = self._load_manifest_sync(path)
                result.append((path, manifest))
            except (OSError, ValueError):
                continue
        return result

    def _segments_sync(self, root: Path) -> list[Path]:
        """Recognize writer segment names, regular files and their first schema."""
        result = []
        try:
            self._assert_path_sync(root)
        except OSError:
            return result
        if not root.is_dir():
            return result
        _, claims = self._owned_files_sync()
        for path in root.iterdir():
            if not _SEGMENT.fullmatch(path.name):
                continue
            try:
                self._assert_path_sync(path)
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
                info = path.lstat()
                identity = (info.st_dev, info.st_ino)
                if self._recognized_segments.get(path) == identity:
                    result.append(path)
                    continue
                claim = claims.get(path.relative_to(self.root).as_posix())
                if self._claim_matches_sync(path, claim):
                    result.append(path)
                    continue
                self._segment_cache.pop(path, None)
                with path.open("rb") as source:
                    first = source.readline(max(self.limits.max_queue_bytes, self.limits.pre_max_bytes, 2 * 1024**2) + 4096)
                record = json.loads(first)
                if not isinstance(record, dict):
                    continue
                schema = str(record.get("schema", ""))
                if schema.startswith(("eg4_local_dongle_trace.", "eg4_controller_observation.", "eg4_incident_trigger.", "eg4_incident_context.")):
                    self._recognized_segments[path] = identity
                    result.append(path)
            except (OSError, ValueError):
                continue
        return result

    @staticmethod
    def _inventory_stamp_sync(path: Path) -> tuple[int, ...] | None:
        """Watch membership/metadata changes without walking every evidence path."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return (
            info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
        )

    def _inventory_sync(self) -> dict[str, list[Path]]:
        """Cache membership only; quota sizes and free space remain live."""
        if self._inventory_cache is not None and all(
            self._inventory_stamp_sync(path) == stamp
            for path, stamp in self._inventory_stamps.items()
        ):
            return {name: list(paths) for name, paths in self._inventory_cache.items()}
        watched = {
            self.root / "recorder.json", self.raw_root,
            self.journal_root, self.incidents_root,
        }
        inventory = {"raw": self._segments_sync(self.raw_root), "journal": self._segments_sync(self.journal_root), "incidents": []}
        for manifest_path, _ in self._manifests_sync():
            inventory["incidents"].append(manifest_path)
            watched.update((
                manifest_path, manifest_path.parent,
                manifest_path.parent / "records", manifest_path.parent / "triggers",
            ))
            for kind in ("records", "triggers"):
                inventory["incidents"].extend(self._segments_sync(manifest_path.parent / kind))
        self._inventory_cache = {name: list(paths) for name, paths in inventory.items()}
        self._inventory_stamps = {path: self._inventory_stamp_sync(path) for path in watched}
        return inventory

    def _stream_limit(self, stream: str) -> tuple[int, float]:
        """Return the single configured byte/age owner for a stream."""
        if stream == "raw":
            return self.limits.raw_max_bytes, self.limits.raw_max_age_seconds
        if stream == "journal":
            return self.limits.journal_max_bytes, self.limits.journal_max_age_seconds
        return self.limits.incident_max_bytes, self.limits.incident_max_age_seconds

    def _remove_sync(
        self, path: Path, stream: str, reason: str, *, retiring: bool = False,
    ) -> None:
        """Remove one owned segment, preserving monotonic incident loss metadata."""
        self._assert_path_sync(path, stream)
        if path.name == "manifest.json":
            return
        info = path.stat()
        size = info.st_size
        if stream == "incidents" and not retiring:
            manifest_path = path.parent.parent / "manifest.json"
            manifest = self._load_manifest_sync(manifest_path, mutable=True)
            relative = path.relative_to(manifest_path.parent).as_posix()
            entries = manifest.setdefault("files", [])
            entry = next((item for item in entries if item["path"] == relative), None)
            if entry is None:
                entry = {"path": relative, "kind": path.parent.name, "bytes": size, "finalized": False}
                entries.append(entry)
            entry["retained"] = False
            entry.pop("sha256", None)
            entry["lost_reason"] = reason
            lost_start, lost_end = self._segment_coverage_sync(path)
            entry["coverage"] = {"start": lost_start, "end": lost_end}
            manifest["complete"], manifest["truncated"] = False, True
            _bounded_add(manifest.setdefault("incomplete_reasons", []), "retention_eviction")
            summaries = manifest.setdefault("retention_losses", {})
            summary = summaries.setdefault(reason, {
                "files": 0, "bytes": 0, "first_observed_at": _iso(self._now()),
                "coverage_start": lost_start,
            })
            summary["files"] += 1
            summary["bytes"] += size
            summary["last_observed_at"] = _iso(self._now())
            if lost_start is not None:
                summary["coverage_start"] = min(summary.get("coverage_start") or lost_start, lost_start)
            if lost_end is not None:
                summary["coverage_end"] = max(summary.get("coverage_end") or lost_end, lost_end)
            _bounded_add(manifest.setdefault("gaps", []), {"reason": reason, "coverage_lost": True, "details": "retention_losses"})
            manifest["evicted_file_count"] = manifest.get("evicted_file_count", 0) + 1
            manifest["evicted_bytes"] = manifest.get("evicted_bytes", 0) + size
            self._compact_lost_files(manifest)
            if path.parent.name == "records":
                context = self._record_segment_context_sync(path)
                self._merge_original_context(manifest, context, context["observed_roles"])
            self._refresh_retained_context_sync(manifest, manifest_path.parent)
            # Persist the loss declaration before unlink. A crash can conservatively
            # underclaim retention, but can never leave a complete manifest after loss.
            self._atomic_json_sync(manifest_path, manifest, reclamation=True)
        self._forget_owned_file_sync(path)
        path.unlink()
        self._segment_cache.pop(path, None)
        self._incident_segment_cache.pop((info.st_dev, info.st_ino), None)
        self._recognized_segments.pop(path, None)
        self._sync_directory_sync(path.parent)
        self._complete_owned_unlink_sync(path)

    def _enforce_space_sync(
        self, stream: str, target: Path, size: int, *, reserve_bytes: int | None = None,
    ) -> None:
        """Serialize actual quotas, expiring raw before consuming incident reserve."""
        self._recover_pending_unlinks_sync()
        if size > self._stream_limit(stream)[0]:
            raise OSError(f"recorder {stream} byte ceiling prevents write")
        reserve_size = size if reserve_bytes is None else reserve_bytes
        inventory = self._inventory_sync()
        now = self._now().timestamp()
        observed: dict[Path, os.stat_result] = {}
        ordered: dict[str, list[Path]] = {}

        def info(path: Path) -> os.stat_result:
            """Share live observations only within this locked quota pass."""
            if path not in observed:
                observed[path] = path.stat()
            return observed[path]

        def candidates(name: str) -> list[Path]:
            """Order safe data candidates, protecting growing incident basenames."""
            if name not in ordered:
                ordered[name] = sorted(
                    (
                        path for path in inventory[name]
                        if path.name != "manifest.json"
                        and (path != target or name != "incidents")
                        and not (name == "incidents" and path.name == "local-dongle.jsonl"
                                 and self._open_bundle_sync(path.parent.parent))
                    ),
                    key=lambda path: info(path).st_mtime,
                )
            return ordered[name]

        def remove(path: Path, name: str, reason: str) -> None:
            """Keep this pass's inventory aligned with actual successful unlinks."""
            self._remove_sync(path, name, reason)
            inventory[name].remove(path)
            observed.clear()
            ordered.clear()

        for name in ("raw", "journal"):
            for path in candidates(name):
                if shutil.disk_usage(self.root).free - reserve_size >= self.limits.min_free_bytes:
                    break
                remove(path, name, "free_space_reserve")
        self._expire_incident_metadata_sync()
        inventory = self._inventory_sync()
        observed.clear()
        ordered.clear()
        for path in candidates("incidents"):
            if shutil.disk_usage(self.root).free - reserve_size >= self.limits.min_free_bytes:
                break
            remove(path, "incidents", "free_space_reserve")
        for name, paths in inventory.items():
            maximum, age = self._stream_limit(name)
            for path in candidates(name):
                if now - info(path).st_mtime > age:
                    remove(path, name, "retention_age")
            extra = size if name == stream else 0
            if target.name == "manifest.json" and name == stream and target.exists():
                extra -= info(target).st_size
            for path in candidates(name):
                if sum(info(item).st_size for item in paths) + extra <= maximum:
                    break
                remove(path, name, "retention_bytes")
                if path == target:
                    extra = size if name == stream else 0
            if sum(info(item).st_size for item in paths) + extra > maximum:
                raise OSError(f"recorder {name} byte ceiling prevents write")
        if shutil.disk_usage(self.root).free - reserve_size < self.limits.min_free_bytes:
            raise OSError("recorder free-space reserve prevents write")

    def _open_bundle_sync(self, directory: Path) -> bool:
        """Protect a growing basename while permitting its rotated history."""
        return self._load_manifest_sync(directory / "manifest.json").get("state") == "open"

    def _prepare_incident_sync(self, manifest: dict[str, Any], path: Path) -> None:
        """Create the manifest before any segment can become a retention candidate."""
        with self._fs_lock:
            self._assert_owner_sync()
            self._assert_path_sync(path, "incidents")
            path.mkdir(parents=True, exist_ok=True)
            self._protected_bundles.add(path)
            self._save_manifest_sync(manifest, path / "manifest.json")
            self._sync_directory_sync(self.incidents_root)

    def _release_incident_sync(self, path: Path) -> None:
        """Release pending-bundle expiry protection only after owned writers close."""
        with self._fs_lock:
            self._protected_bundles.discard(path)

    def _expire_incident_metadata_sync(self) -> None:
        """Retire only aged closed/interrupted bundles and their recognized files."""
        now = self._now()
        for path, manifest in self._manifests_sync():
            directory = path.parent
            closed = _date(manifest.get("closed_at"))
            if (
                directory in self._protected_bundles
                or manifest.get("state") not in ("closed", "interrupted")
                or closed is None
                or (now - closed).total_seconds() <= self.limits.incident_max_age_seconds
            ):
                continue
            if not manifest.get("retention_expired"):
                # Replace expensive context with an explicit expired tombstone
                # before removing any bytes. A crash during retirement therefore
                # leaves an incomplete declaration rather than a complete bundle.
                retired = {
                    key: manifest.get(key) for key in (
                        "schema", "incident_id", "session_id", "build_id", "entry_id",
                        "evidence_class", "state", "started_at", "last_trigger_at", "closed_at",
                        "expected_roles",
                    )
                }
                old_coverage = manifest.get("coverage", {})
                retired.update({
                    "retention_expired": True, "retired_at": _iso(now),
                    "complete": False, "truncated": True, "incomplete_reasons": ["retention_expired"],
                    "observed_roles": [], "missing_roles": manifest.get("expected_roles", []),
                    "coverage": {
                        "requested_pre_seconds": old_coverage.get("requested_pre_seconds"),
                        "requested_post_seconds": old_coverage.get("requested_post_seconds"),
                        "actual_start": None, "actual_end": None, "pre_start": None, "pre_end": None,
                        "warmup": old_coverage.get("warmup", True),
                        "memory_truncated": old_coverage.get("memory_truncated", False),
                    },
                    "configuration_context": {}, "errors": [],
                    "gaps": [{"reason": "retention_expired", "observed_at": _iso(now)}],
                    "triggers": [], "trigger_count": manifest.get("trigger_count", 0),
                    "trigger_files": [], "files": [],
                })
                self._atomic_json_sync(path, retired, reclamation=True)
            for kind in ("records", "triggers"):
                for segment in self._segments_sync(directory / kind):
                    self._remove_sync(segment, "incidents", "retention_expired", retiring=True)
            owner, claims = self._owned_files_sync(mutable=True)
            prefix = directory.relative_to(self.root).as_posix() + "/"
            changed = False
            for relative in tuple(claims):
                if relative.startswith(prefix):
                    claim = claims[relative]
                    staging_name = claim.get("stage") if isinstance(claim, dict) else None
                    if isinstance(staging_name, str) and re.fullmatch(r"\.local-dongle-[0-9a-f]{32}\.pending", staging_name):
                        staging = (self.root / relative).parent / staging_name
                        try:
                            if self._claim_matches_sync(staging, claim):
                                staging.unlink()
                                self._sync_directory_sync(staging.parent)
                        except OSError:
                            if not any(candidate.is_symlink() for candidate in (staging, *staging.parents)):
                                raise
                    del claims[relative]
                    changed = True
            if changed:
                self._atomic_json_sync(self.root / "recorder.json", owner, reclamation=True)
            path.unlink()
            self._manifest_cache.pop(path, None)
            self._sync_directory_sync(directory)
            for candidate in (directory / "records", directory / "triggers", directory):
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                try:
                    candidate.rmdir()
                except OSError as err:
                    if err.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                        raise
            self._sync_directory_sync(self.incidents_root)

    def _merge_losses(self, manifest: dict[str, Any], existing: dict[str, Any]) -> None:
        """Prevent stale active snapshots from overwriting retention/crash truth."""
        for reason in existing.get("incomplete_reasons", ()):
            _bounded_add(manifest["incomplete_reasons"], reason)
        for gap in existing.get("gaps", ()):
            _bounded_add(manifest["gaps"], gap)
        manifest["truncated"] |= bool(existing.get("truncated"))
        self._merge_original_context(manifest, existing.get("original_coverage", existing.get("coverage", {})), existing.get("original_observed_roles", existing.get("observed_roles", ())))
        lost = {item["path"]: item for item in existing.get("files", ()) if not item.get("retained", True)}
        entries = {item["path"]: item for item in manifest.get("files", ())}
        entries.update(lost)
        for key in ("evicted_file_count", "evicted_bytes"):
            manifest[key] = max(manifest.get(key, 0), existing.get(key, 0))
        for reason, summary in existing.get("retention_losses", {}).items():
            current = manifest.setdefault("retention_losses", {}).get(reason)
            if current is None or current.get("files", 0) < summary.get("files", 0):
                manifest["retention_losses"][reason] = summary
        manifest["files"] = list(entries.values())
        if manifest["incomplete_reasons"] or manifest["missing_roles"] or manifest["truncated"]:
            manifest["complete"] = False
        manifest["trigger_files"] = [
            item["path"] for item in manifest["files"]
            if item["kind"] == "triggers" and item.get("retained", True)
        ]
        self._compact_lost_files(manifest)

    def _compact_lost_files(self, manifest: dict[str, Any]) -> None:
        """Bound file tombstones while retaining cumulative lost-byte truth."""
        retained = [item for item in manifest.get("files", ()) if item.get("retained", True)]
        lost = [item for item in manifest.get("files", ()) if not item.get("retained", True)]
        if len(lost) > _MAX_SUMMARIES:
            manifest["compacted_lost_file_entries"] = max(
                manifest.get("compacted_lost_file_entries", 0), len(lost) - _MAX_SUMMARIES,
            )
        manifest["files"] = retained + lost[-_MAX_SUMMARIES:]

    def _save_manifest_sync(
        self, manifest: dict[str, Any], path: Path, *, synchronize: bool = True,
    ) -> None:
        """Merge monotonic disk loss and enforce budget before atomic replacement."""
        self._assert_path_sync(path, "incidents")
        if path.exists():
            self._merge_losses(manifest, self._load_manifest_sync(path, mutable=True))
        self._refresh_retained_context_sync(manifest, path.parent)
        while True:
            payload_size = len((json.dumps(manifest, separators=(",", ":")) + "\n").encode())
            self._enforce_space_sync("incidents", path, payload_size)
            # Pruning may grow this bundle's loss metadata. Recalculate until
            # the exact merged payload, not a stale smaller one, fits its budget.
            if path.exists():
                self._merge_losses(manifest, self._load_manifest_sync(path, mutable=True))
            self._refresh_retained_context_sync(manifest, path.parent)
            updated_size = len((json.dumps(manifest, separators=(",", ":")) + "\n").encode())
            if updated_size == payload_size:
                break
        self._atomic_json_sync(path, manifest, synchronize=synchronize)

    def _merge_coverage(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        """Keep an interval envelope and per-role intervals without zero filling."""
        for key in ("actual_start", "pre_start", "actual_end", "pre_end"):
            value = source.get(key)
            if isinstance(value, str):
                previous = target.get(key)
                target[key] = value if previous is None else (
                    min(previous, value) if key.endswith("start") else max(previous, value)
                )
        for role, value in source.get("by_role", {}).items():
            current = target.setdefault("by_role", {}).setdefault(role, dict(value))
            current["start"] = min(current["start"], value["start"])
            current["end"] = max(current["end"], value["end"])

    def _merge_original_context(
        self, manifest: dict[str, Any], coverage: dict[str, Any], roles: Any,
    ) -> None:
        """Preserve original/lost custody separately from current retained coverage."""
        original = manifest.setdefault("original_coverage", deepcopy(manifest["coverage"]))
        self._merge_coverage(original, coverage)
        observed = set(manifest.get("original_observed_roles", manifest.get("observed_roles", ())))
        observed.update(roles)
        manifest["original_observed_roles"] = sorted(observed)

    def _record_segment_context_sync(self, path: Path) -> dict[str, Any]:
        """Scan only new segment bytes and cache bounded source/usable-role summaries."""
        info = path.stat()
        identity = (info.st_dev, info.st_ino)
        cached = self._incident_segment_cache.get(identity)
        if cached is not None and cached[:2] == (info.st_size, info.st_mtime_ns):
            return cached[2]
        growing = cached is not None and info.st_size > cached[0]
        summary: dict[str, Any] = deepcopy(cached[2]) if cached is not None and growing else {
            "actual_start": None, "actual_end": None, "pre_start": None, "pre_end": None,
            "by_role": {}, "observed_roles": set(), "malformed": 0,
        }
        with path.open("rb") as source:
            if cached is not None and growing:
                source.seek(cached[0])
            ceiling = max(self.limits.max_queue_bytes, self.limits.pre_max_bytes, 2 * 1024**2) + 4096
            while line := source.readline(ceiling):
                if not line.endswith(b"\n"):
                    while line and not line.endswith(b"\n"):
                        line = source.readline(ceiling)
                    summary["malformed"] += 1
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("non-object incident record")
                except (ValueError, TypeError):
                    summary["malformed"] += 1
                    continue
                if record.get("kind") == "incident_context":
                    continue
                capture = record.get("incident_capture") or {}
                start, end = _interval(record, _date(capture.get("received_at")) or self._now())
                interval = {"actual_start": start, "actual_end": end}
                if capture.get("pretrigger"):
                    interval.update(pre_start=start, pre_end=end)
                self._merge_coverage(summary, interval)
                role = record.get("role")
                if isinstance(role, str):
                    current = summary["by_role"].setdefault(role, {"start": start, "end": end})
                    current["start"], current["end"] = min(current["start"], start), max(current["end"], end)
                    if record.get("kind", "acquisition") == "acquisition" and record.get("ok") is True and record.get("schema") != "eg4_controller_observation.v1":
                        summary["observed_roles"].add(role)
        self._incident_segment_cache[identity] = (info.st_size, info.st_mtime_ns, summary)
        return summary

    def _refresh_retained_context_sync(
        self, manifest: dict[str, Any], directory: Path,
    ) -> None:
        """Derive current coverage and usable roles exclusively from retained files."""
        self._merge_original_context(manifest, manifest["coverage"], manifest.get("observed_roles", ()))
        retained: dict[str, Any] = {
            "actual_start": None, "actual_end": None, "pre_start": None, "pre_end": None,
            "by_role": {},
        }
        observed: set[str] = set()
        expected = set(manifest["expected_roles"])
        entries = {item["path"]: item for item in manifest.get("files", ())}
        actual: set[str] = set()
        for kind in ("records", "triggers"):
            for path in self._segments_sync(directory / kind):
                relative = path.relative_to(directory).as_posix()
                actual.add(relative)
                existing = entries.get(relative)
                if existing is not None and not existing.get("retained", True):
                    continue
                size = path.stat().st_size
                if existing is None:
                    entries[relative] = {"path": relative, "kind": kind, "bytes": size, "retained": True, "finalized": False}
                else:
                    if existing["bytes"] != size:
                        existing["finalized"] = False
                        existing.pop("sha256", None)
                    existing["bytes"] = size
                if kind == "records":
                    summary = self._record_segment_context_sync(path)
                    self._merge_coverage(retained, summary)
                    observed.update(summary["observed_roles"])
                    if summary["malformed"]:
                        _bounded_add(manifest["incomplete_reasons"], "incident_partial_jsonl")
        for relative, entry in entries.items():
            if entry.get("retained", True) and relative not in actual:
                entry["retained"], entry["finalized"] = False, False
                entry.pop("sha256", None)
                entry["lost_reason"] = "segment_missing"
                manifest["truncated"] = True
                _bounded_add(manifest["incomplete_reasons"], "incident_segment_missing")
        retained["by_role"] = {role: value for role, value in retained["by_role"].items() if role in expected}
        observed &= expected
        self._merge_original_context(manifest, retained, observed)
        manifest["coverage"].update(retained)
        manifest["observed_roles"], manifest["missing_roles"] = sorted(observed), sorted(expected - observed)
        manifest["files"] = list(entries.values())
        manifest["trigger_files"] = [item["path"] for item in entries.values() if item["kind"] == "triggers" and item.get("retained", True)]
        manifest["complete"] = (
            manifest["state"] == "closed" and not manifest["incomplete_reasons"]
            and not manifest["truncated"] and not manifest["missing_roles"]
            and all(item.get("finalized") and item.get("sha256") for item in entries.values() if item.get("retained", True))
        )

    def _checkpoint_sync(
        self, manifest: dict[str, Any], path: Path, finalized: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Inspect actual files and hash only synchronized, immutable final segments."""
        with self._fs_lock:
            self._assert_owner_sync()
            self._assert_path_sync(path, "incidents")
            path.mkdir(parents=True, exist_ok=True)
            entries = {item["path"]: item for item in manifest.get("files", ())}
            for relative, entry in entries.items():
                if not entry.get("retained", True):
                    continue
                candidate = path / relative
                try:
                    self._assert_path_sync(candidate, "incidents")
                    missing = not candidate.is_file()
                except OSError:
                    missing = True
                if missing:
                    entry["retained"], entry["finalized"] = False, False
                    entry.pop("sha256", None)
                    entry["lost_reason"] = "segment_missing"
                    manifest["complete"], manifest["truncated"] = False, True
                    _bounded_add(manifest["incomplete_reasons"], "incident_segment_missing")
                    _bounded_add(manifest["gaps"], {"reason": "segment_missing", "path": relative})
            success = True
            for kind in ("records", "triggers"):
                directory = path / kind
                for segment in self._segments_sync(directory):
                    relative = segment.relative_to(path).as_posix()
                    old = entries.get(relative)
                    if old is not None and old.get("retained") is False:
                        # The rolling active basename may have been recreated after
                        # pressure; preserve lost coverage, but account new bytes too.
                        _bounded_add(manifest["gaps"], {"reason": "segment_recreated_after_eviction", "path": relative})
                    entry: dict[str, Any] = {"path": relative, "kind": kind, "bytes": segment.stat().st_size, "retained": True, "finalized": False}
                    if finalized:
                        try:
                            digest = hashlib.sha256()
                            with segment.open("rb") as source:
                                os.fsync(source.fileno())
                                for chunk in iter(lambda: source.read(1024**2), b""):
                                    digest.update(chunk)
                            self._sync_directory_sync(directory)
                            entry["sha256"], entry["finalized"] = digest.hexdigest(), True
                        except OSError as err:
                            success = False
                            _bounded_add(manifest["errors"], f"finalization: {type(err).__name__}: {err}")
                    entries[relative] = entry
            manifest["files"] = list(entries.values())
            manifest["trigger_files"] = [item["path"] for item in manifest["files"] if item["kind"] == "triggers" and item.get("retained")]
            if not success:
                manifest["complete"] = False
                _bounded_add(manifest["incomplete_reasons"], "incident_hash_or_sync_failed")
            try:
                self._save_manifest_sync(manifest, path / "manifest.json")
            except OSError:
                # A post-rename directory-sync failure must not leave a claim of
                # finalized custody. Best-effort atomic correction is explicitly
                # unsynchronized, and current degraded health retains the failure.
                manifest["complete"] = False
                _bounded_add(manifest["incomplete_reasons"], "manifest_synchronization_failed")
                for entry in manifest["files"]:
                    entry["finalized"] = False
                    entry.pop("sha256", None)
                self._save_manifest_sync(manifest, path / "manifest.json", synchronize=False)
                raise
            if not success:
                raise OSError("incident file hashing or synchronization failed")
            return manifest, self._metrics_sync()

    def _segment_coverage_sync(self, path: Path) -> tuple[str | None, str | None]:
        """Scan new persisted lines once, caching actual min/max source intervals."""
        info = path.stat()
        cached = self._segment_cache.get(path)
        if cached is not None and cached[:2] == (info.st_size, info.st_mtime_ns):
            return cached[2], cached[3]
        growing = cached is not None and info.st_size > cached[0]
        oldest, newest = (cached[2], cached[3]) if cached is not None and growing else (None, None)
        with path.open("rb") as source:
            if cached is not None and growing:
                source.seek(cached[0])
            ceiling = max(self.limits.max_queue_bytes, self.limits.pre_max_bytes, 2 * 1024**2) + 4096
            while line := source.readline(ceiling):
                if not line.endswith(b"\n"):
                    while line and not line.endswith(b"\n"):
                        line = source.readline(ceiling)
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if not any(_date(record.get(key)) is not None for key in ("started_at", "sample_started_at", "observed_at")):
                        continue
                    start, end = _interval(record, self._now())
                except (ValueError, TypeError):
                    continue
                oldest = min(oldest or start, start)
                newest = max(newest or end, end)
        self._segment_cache[path] = (info.st_size, info.st_mtime_ns, oldest, newest)
        return oldest, newest

    def _metrics_sync(self) -> dict[str, Any]:
        """Sample retained bytes/free space and actual persisted raw source coverage."""
        inventory = self._inventory_sync()
        metrics: dict[str, Any] = {
            "RawOldest": None, "RawNewest": None, "StorageObservedAt": _iso(self._now()),
        }
        for name, label in (("raw", "RawBytes"), ("journal", "JournalBytes"), ("incidents", "IncidentBytes")):
            metrics[label] = sum(path.stat().st_size for path in inventory[name])
        for path in inventory["raw"]:
            oldest, newest = self._segment_coverage_sync(path)
            if oldest is not None:
                metrics["RawOldest"] = min(metrics["RawOldest"] or oldest, oldest)
            if newest is not None:
                metrics["RawNewest"] = max(metrics["RawNewest"] or newest, newest)
        retained = set(inventory["raw"])
        self._segment_cache = {path: value for path, value in self._segment_cache.items() if path in retained}
        all_retained = {path for paths in inventory.values() for path in paths}
        self._recognized_segments = {path: value for path, value in self._recognized_segments.items() if path in all_retained}
        retained_identities = {
            (info.st_dev, info.st_ino)
            for path in inventory["incidents"] if path.name != "manifest.json"
            for info in (path.stat(),)
        }
        self._incident_segment_cache = {
            identity: summary for identity, summary in self._incident_segment_cache.items()
            if identity in retained_identities
        }
        metrics["FreeBytes"] = shutil.disk_usage(self.root).free
        oldest, newest = _date(metrics["RawOldest"]), _date(metrics["RawNewest"])
        seconds = (newest - oldest).total_seconds() if oldest and newest else 0
        rate = metrics["RawBytes"] / seconds if seconds > 0 else None
        capacity = min(self.limits.raw_max_bytes, metrics["RawBytes"] + max(0, metrics["FreeBytes"] - self.limits.min_free_bytes))
        metrics["EstimatedRawRetentionHours"] = min(self.limits.raw_max_age_seconds / 3600, capacity / rate / 3600) if rate else None
        return metrics

    def _retention_metrics_sync(self, open_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Expire owned files and refresh a compact cache at health-tick frequency."""
        with self._fs_lock:
            # Rescan at health cadence even if an external producer restored a
            # manifest inside an already existing, previously unrecognized dir.
            self._inventory_cache = None
            self._assert_owner_sync()
            if open_ids is not None:
                for path, manifest in self._manifests_sync():
                    if manifest.get("state") == "open" and manifest.get("incident_id") not in open_ids:
                        manifest = deepcopy(manifest)
                        manifest["state"] = "interrupted"
                        manifest["complete"] = False
                        manifest["closed_at"] = _iso(self._now())
                        _bounded_add(manifest.setdefault("incomplete_reasons", []), "incident_finalization_interrupted")
                        self._recover_bundle_sync(path.parent, manifest)
                        self._save_manifest_sync(manifest, path)
            self._enforce_space_sync("raw", self.raw_root / ".retention-probe", 0)
            return self._metrics_sync()
