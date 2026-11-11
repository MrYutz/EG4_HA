#!/usr/bin/env python3
"""Stream read-only EG4 trace files, recorder roots and incident directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeGuard

from eg4_trace_coverage import Coverage

MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 16384
MAX_ROLES = 64
MAX_CONTROLLER_SOURCES = 2048


def _safe_path(path: Path, root: Path | None = None) -> Path:
    """Reject traversal, symlinks and escapes before accessing evidence."""
    if ".." in path.parts:
        raise ValueError(f"unsafe_path: {path}")
    absolute = path.absolute()
    if root is not None and not absolute.is_relative_to(root.absolute()):
        raise ValueError(f"unsafe_path: {path}")
    for part in (absolute, *absolute.parents):
        if part.is_symlink():
            raise ValueError(f"unsafe_path: symlink {part}")
    return absolute


def _open_evidence(path: Path, root: Path | None = None) -> BinaryIO:
    """Open a regular evidence file read-only without following symlinks."""
    path = _safe_path(path, root)
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
        )
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"unsafe_path: not a regular file: {path}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _json_document(path: Path) -> dict[str, Any]:
    """Read one bounded metadata document without modifying its source."""
    with _open_evidence(path) as source:
        payload = source.read(MAX_RECORD_BYTES + 1)
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError(f"{path}: metadata exceeds {MAX_RECORD_BYTES} bytes")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return result


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    """Stream bounded JSONL records, retaining source line diagnostics."""
    with _open_evidence(path) as source:
        line_number = 0
        while line := source.readline(MAX_RECORD_BYTES + 1):
            line_number += 1
            if len(line) > MAX_RECORD_BYTES:
                raise ValueError(f"{path}:{line_number}: record exceeds {MAX_RECORD_BYTES} bytes")
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield row


def _evidence_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Adapt retained direct-baseline wrappers without inventing transport IDs."""
    if path.suffix != ".json":
        yield from _rows(path)
        return
    document = _json_document(path)
    schema = document.get("schema")
    supported = {
        "eg4_extended_baseline.v1", "eg4_gridboss_configuration_baseline.v1",
    }
    if schema not in supported and not (
        isinstance(document.get("role"), str) and isinstance(document.get("reads"), list)
    ):
        raise ValueError(f"{path}: unsupported baseline document schema: {schema!r}")
    role = document.get("role")
    if role is None and schema == "eg4_gridboss_configuration_baseline.v1":
        role = "gridboss"  # Explicit schema identity, never a filename guess.
    context = {
        key: document[key] for key in ("build_id", "session_id", "register_map_revision", "captured_at")
        if key in document
    }
    yield {**context, "kind": "baseline_context", "role": role, "baseline_schema": schema}
    collections = [_mapping(document.get("initial_q1_readonly")).get("transactions")]
    collections.extend(
        document.get(key) for key in ("reads", "blocks", "additional_blocks", "requests")
    )
    for blocks in collections:
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and block.get("function") in (3, 4):
                yield {
                    **context, **block, "kind": "direct_baseline", "role": role,
                    "baseline_schema": schema,
                }


def _segment_order(path: Path) -> tuple[bool, str]:
    """Read timestamp-named rotations before their still-active basename."""
    return path.name == "local-dongle.jsonl", path.name


def _children(root: Path) -> list[Path]:
    """Bound filename metadata and order rotated files before the active file."""
    _safe_path(root)
    children = []
    with os.scandir(root) as entries:
        for entry in entries:
            if len(children) >= MAX_DIRECTORY_ENTRIES:
                raise ValueError(f"{root}: directory entry limit exceeded; analysis incomplete")
            children.append(Path(entry.path))
    return sorted(children, key=_segment_order)


def _trace_files(root: Path, *, historical: bool = False) -> Iterator[Path]:
    """Select plain trace families, never unrelated historical documents."""
    if not root.exists():
        return
    for path in _children(root):
        if path.suffix != ".jsonl":
            continue
        if historical and not path.name.startswith(("local-dongle", "controller-observation")):
            continue
        # Validate each leaf when opening it, so directory consumers can report
        # a rejected file and still inspect the other safe retained segments.
        yield path


def _mapping(value: Any) -> dict[str, Any]:
    """Treat missing and malformed optional objects as unavailable."""
    return value if isinstance(value, dict) else {}


def _interval(row: dict[str, Any]) -> tuple[Any, Any]:
    """Return original source intervals without substituting persistence time."""
    return (
        row.get("started_at") or row.get("sample_started_at"),
        row.get("completed_at") or row.get("sample_completed_at"),
    )


def _timestamp(value: Any) -> datetime | None:
    """Accept only timezone-aware host timestamps."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _context(row: dict[str, Any]) -> str:
    """Render original source identity and acquisition interval."""
    start, end = _interval(row)
    return (
        f"role={row.get('role', 'unknown')} "
        f"session={row.get('session_id') or 'unavailable'} "
        f"acquisition={row.get('acquisition_id') or 'unavailable'} "
        f"transaction={row.get('transaction_id') or 'unavailable'} "
        f"start={start or 'unavailable'} end={end or 'unavailable'}"
    )


def _event(name: str, row: dict[str, Any], **details: Any) -> None:
    """Emit one streaming evidence event, never a physical interpretation."""
    print(f"  {name} {_context(row)} details={json.dumps(details, sort_keys=True)}")


@dataclass
class NumericSummary:
    """Retain constant-space statistics, never all samples or trace rows."""

    count: int = 0
    total: float = 0
    minimum: float = math.inf
    maximum: float = -math.inf
    latest: float = 0

    def add(self, value: Any) -> None:
        """Accumulate only finite reported values; unknown is never zero."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if not math.isfinite(value):
            return
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.latest = value

    def report(self, name: str) -> None:
        """Print compact numeric coverage without storing the sample corpus."""
        if self.count:
            print(
                f"  {name}=min:{self.minimum:.3f} avg:{self.total / self.count:.3f} "
                f"max:{self.maximum:.3f} latest:{self.latest:.3f} n:{self.count}"
            )
        else:
            print(f"  {name}=unavailable n:0")


def _compact_context(value: Any, depth: int = 0) -> Any:
    """Keep source/age metadata, omitting bulk register and telemetry payloads."""
    if depth > 8:
        return "context_depth_limit"
    if isinstance(value, dict):
        return {
            key: _compact_context(item, depth + 1)
            for key, item in list(value.items())[:256]
            if key not in ("values", "telemetry", "frames", "raw", "ha_states")
        }
    if isinstance(value, list):
        return [_compact_context(item, depth + 1) for item in value[:64]]
    return value


def _configuration(row: dict[str, Any]) -> None:
    """Keep first and emitted later snapshots distinct, including original ages."""
    emitted = row.get("kind") == "incident_context"
    context = {
        key: row[key] for key in ("roles", "role_freshness", "configuration_observed_at")
        if key in row
    } if emitted else row.get("configuration_context")
    if context:
        _event(
            "incident_context" if emitted else "configuration_context", row,
            context=_compact_context(context),
            configuration_observed_at=(
                _mapping(context).get("configuration_observed_at")
                or row.get("configuration_observed_at")
            ),
            effective_change_time_known=False,
            observed_at=row.get("observed_at"),
            incident_id=row.get("incident_id"),
        )


def _bracket(previous: tuple[Any, Any] | None, current: tuple[Any, Any]) -> dict[str, Any]:
    """Retain host intervals without locating a physical transition."""
    end = _timestamp(previous[1]) if previous else None
    start = _timestamp(current[0])
    return {
        "previous_interval": previous, "current_interval": current,
        "ordering": "unresolved" if end is None or start is None or end >= start
        else "host_observation_bracket",
        "transition_time": "unresolved", "continuity": "unknown",
    }


def _number(value: Any) -> TypeGuard[int | float]:
    """Recognize a finite, nonboolean reported number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _controller_words(row: dict[str, Any]) -> dict[str, Any]:
    """Extract discrete raw facts, not firmware meanings or contact feedback."""
    gridboss = row.get("role") == "gridboss"
    inputs = (
        (*range(122, 128), 131, *range(500, 520),
         567, 568, 569, 572, 576, 577, 578, 579, 580, 587, 588)
        if gridboss else (0, 6, 60, 61, 62, 63, 77, 144, 145)
    )
    holdings = (
        (18, 20, 24, 37, 38, 170, 179, 226, 2102, *range(4500, 4510))
        if gridboss else (110, 170, 177, 179, 194, 195, 196, 197, 226)
    )
    result = {}
    for name, values in _mapping(_mapping(row.get("telemetry")).get("raw")).items():
        if not isinstance(values, list):
            continue
        if name.startswith("input_registers_start_"):
            start = name.removeprefix("input_registers_start_")
        elif name.startswith("frequency_registers_start_"):
            start = name.removeprefix("frequency_registers_start_")
        else:
            continue
        if start.isdecimal():
            for register in inputs:
                offset = register - int(start)
                if 0 <= offset < len(values):
                    word = values[offset]
                    if type(word) is int and 0 <= word <= 65535:
                        result[f"input.{register}"] = word
    for block in (*(row.get("primary_blocks") or []), *(row.get("diagnostics") or [])):
        if not isinstance(block, dict) or block.get("ok") is not True:
            continue
        if (block.get("partial") is True
                or any(value != "passed" for value in _mapping(block.get("validation")).values())
                or _mapping(block.get("quality")).get("structural") == "rejected"):
            continue
        values, start = block.get("values"), block.get("register")
        if not isinstance(values, list) or type(start) is not int:
            continue
        count = block.get("count")
        if count is not None and (type(count) is not int or count <= 0 or len(values) != count):
            continue
        function = block.get("function")
        for register in inputs if function == 4 else holdings if function == 3 else ():
            offset = register - start
            if 0 <= offset < len(values):
                word = values[offset]
                if type(word) is int and 0 <= word <= 65535:
                    result[f"{'input' if function == 4 else 'holding'}.{register}"] = word
    if not gridboss and type(result.get("input.6")) is int:
        result["input.6.bits_0_1"] = result["input.6"] & 3
    return result


@dataclass
class RoleSummary:
    """Summarize one role using bounded state and original observation brackets."""
    rows: int = 0
    valid: int = 0
    errors: int = 0
    core_samples: int = 0
    transaction_failures: int = 0
    partial_transactions: int = 0
    rejections: int = 0
    raw_rejections: int = 0
    transitions: int = 0
    kinds: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    transfer_states: Counter = field(default_factory=Counter)
    numbers: dict[str, NumericSummary] = field(default_factory=lambda: {
        name: NumericSummary()
        for name in ("gap_s", "uncovered_s", "duration_ms", "charge_w", "discharge_w")
    })
    first: datetime | None = None
    last: datetime | None = None
    previous_session: str | None = None
    previous_end: datetime | None = None
    status: Any = None
    status_start: dict[str, Any] | None = None
    status_end: Any = None

    acquisition_partials: int = 0
    acquisition_failures: int = 0
    acquisition_gaps: int = 0
    session_segments: int = 0
    gap_seconds: float = 5
    source_interval: tuple[Any, Any] | None = None
    source_first: datetime | None = None
    source_last: datetime | None = None
    outputs: dict[str, tuple[bool, str, tuple[Any, Any], str | None]] = field(default_factory=dict)
    controller: dict[str, Any] = field(default_factory=dict)
    controller_sources: dict[tuple[Any, ...], None] = field(default_factory=dict)
    controller_provenance: dict[str, tuple[Any, Any, tuple[Any, Any]]] = field(default_factory=dict)

    def _outputs(self, row: dict[str, Any], telemetry: dict[str, Any]) -> None:
        """Inspect reported 240-V output, including raw words from failed decodes."""
        decoded, core = _mapping(telemetry.get("decoded")), _mapping(telemetry.get("core"))
        channel, value = (
            ("ups_voltage_v", decoded.get("ups_voltage_v"))
            if row.get("role") == "gridboss"
            else ("eps_voltage_r_v", core.get("eps_voltage_r_v"))
        )
        event_row = row
        value_source = "decoded_software"
        interval = _interval(_mapping(row.get("primary_source")) or row)
        if not _number(value):
            output_register = 2 if row.get("role") == "gridboss" else 20
            for block in row.get("primary_blocks") or []:
                if not isinstance(block, dict) or block.get("ok") is not True:
                    continue
                if block.get("function") != 4 or block.get("current_acquisition") is False:
                    continue
                if (row.get("acquisition_id") is not None
                        and block.get("acquisition_id") != row["acquisition_id"]):
                    continue
                values, start = block.get("values"), block.get("register")
                if not isinstance(values, list) or type(start) is not int:
                    continue
                offset = output_register - start
                if not 0 <= offset < len(values):
                    continue
                word = values[offset]
                if type(word) is not int or not 0 <= word <= 65535:
                    continue
                value = word / 10
                value_source = "structural_raw_register"
                interval = _interval(block)
                event_row = {
                    name: row[name] for name in ("role", "session_id", "acquisition_id")
                    if name in row
                }
                event_row.update({
                    name: block.get(name)
                    for name in ("transaction_id", "started_at", "completed_at")
                })
                break
            else:
                return
        previous = self.outputs.get(channel)
        source_id = event_row.get("acquisition_id")
        source_id = source_id if isinstance(source_id, str) and source_id else None
        if source_id is not None and previous is not None and previous[3] == source_id:
            return
        live = bool(previous and previous[0]) or value >= 180
        state = "high" if value > 264 else "low" if value < 180 and live else "normal"
        prior_interval = previous[2] if previous else None
        if state != "normal":
            _event(
                "output_voltage_anomaly", event_row, channel=channel, voltage_v=value,
                anomaly=state, reported_software_value=True, value_source=value_source,
                **{
                    "onset_bracket" if previous is None or previous[1] != state
                    else "observation_bracket": _bracket(prior_interval, interval),
                },
            )
        elif previous and previous[1] != "normal":
            _event(
                "output_voltage_recovered", event_row, channel=channel, voltage_v=value,
                value_source=value_source,
                recovery_bracket=_bracket(prior_interval, interval),
            )
        self.outputs[channel] = live, state, interval, source_id

    def _source(self, row: dict[str, Any]) -> None:
        """Measure acquisition coverage and gaps without filling missing data."""
        interval = _interval(_mapping(row.get("primary_source")) or row)
        start, end = map(_timestamp, interval)
        if start is not None:
            self.source_first = min(self.source_first, start) if self.source_first else start
        if end is not None:
            self.source_last = max(self.source_last, end) if self.source_last else end
        if self.source_interval is not None:
            previous_end = _timestamp(self.source_interval[1])
            if start is not None and previous_end is not None:
                gap = (start - previous_end).total_seconds()
                self.numbers["uncovered_s"].add(gap if gap > 0 else None)
                if gap > self.gap_seconds:
                    self.acquisition_gaps += 1
                    _event(
                        "acquisition_gap", row, observation_gap_s=gap,
                        bracket=_bracket(self.source_interval, interval), continuity="unknown",
                    )
                elif gap < 0:
                    _event("interval_order_unresolved", row, bracket=_bracket(self.source_interval, interval))
        self.source_interval = interval

    def _controller_values(
        self, row: dict[str, Any], current: dict[str, Any], *,
        shape: tuple[Any, ...] = (), cached: bool = False,
    ) -> None:
        """Deduplicate source facts without discarding new raw evidence on clock jumps."""
        transaction = row.get("transaction_id")
        acquisition = row.get("acquisition_id")
        transaction = transaction if isinstance(transaction, str) and transaction else None
        acquisition = acquisition if isinstance(acquisition, str) and acquisition else None
        source = (
            ("transaction", transaction, *shape) if transaction else
            ("acquisition", acquisition, *shape) if acquisition else None
        )
        if source is not None:
            if source in self.controller_sources:
                return
            self.controller_sources[source] = None
            if len(self.controller_sources) > MAX_CONTROLLER_SOURCES:
                del self.controller_sources[next(iter(self.controller_sources))]
        interval = _interval(row)
        observed_at = _timestamp(interval[1]) or _timestamp(interval[0])
        accepted = {}
        brackets = {}
        for key, value in current.items():
            previous = self.controller_provenance.get(key)
            if previous is not None:
                prior_transaction, prior_acquisition, prior_interval = previous
                if ((transaction and transaction == prior_transaction)
                        or (not transaction and acquisition and acquisition == prior_acquisition)):
                    continue
                prior_time = _timestamp(prior_interval[1]) or _timestamp(prior_interval[0])
                if observed_at is not None and prior_time is not None:
                    if observed_at <= prior_time and row.get("kind") != "transaction":
                        continue
                elif cached:
                    # Undated cached context cannot establish a newer observation.
                    continue
            if key in self.controller and self.controller[key] != value:
                brackets[key] = _bracket(previous[2] if previous else None, interval)
            accepted[key] = value
            self.controller_provenance[key] = transaction, acquisition, interval
        changed = {
            key: {"previous": self.controller[key], "current": value}
            for key, value in accepted.items()
            if key in self.controller and self.controller[key] != value
        }
        if changed:
            _event(
                "controller_protection_change", row, changed=changed, authority="raw_words",
                observation_brackets=brackets,
            )
        self.controller.update(accepted)

    def _controller(self, row: dict[str, Any], *, availability: bool = True) -> None:
        """Compare raw blocks at their own source times, not summary publication times."""
        blocks = [
            block for block in (*(row.get("primary_blocks") or []), *(row.get("diagnostics") or []))
            if isinstance(block, dict)
        ]
        for block in blocks:
            current = _controller_words({"role": row.get("role"), "primary_blocks": [block]})
            if not current:
                continue
            event_row = {**row}
            if any(block.get(name) is not None for name in (
                "acquisition_id", "transaction_id", "started_at", "completed_at",
            )):
                event_row.update({
                    name: block.get(name) for name in (
                        "acquisition_id", "transaction_id", "started_at", "completed_at",
                    )
                })
                # Missing block times remain missing, never summary/persistence times.
                event_row.pop("sample_started_at", None)
                event_row.pop("sample_completed_at", None)
            if block.get("session_id") is not None:
                if block["session_id"] != row.get("session_id"):
                    continue
                event_row["session_id"] = block["session_id"]
            self._controller_values(
                event_row, current,
                shape=tuple(block.get(name) for name in ("function", "register", "count")),
                cached=block.get("current_acquisition") is False,
            )
        if not blocks and row.get("ok") is True:
            # Historical records have raw telemetry but no source block metadata.
            self._controller_values(row, _controller_words(row))
        if not availability:
            return
        current = {}
        if isinstance(row.get("ok"), bool):
            current["availability.primary"] = row["ok"]
        for block in row.get("diagnostics") or []:
            if not isinstance(block, dict) or block.get("current_acquisition") is False:
                continue
            if not isinstance(block.get("ok"), bool):
                continue
            key = "availability.diagnostic_raw." + ":".join(
                str(block.get(name)) for name in ("function", "register", "count")
            )
            if key in self.controller or len(self.controller) + len(current) < 128:
                current[key] = block["ok"]
        self._controller_values(row, current, shape=("availability",))

    def _close_status(self) -> None:
        """Finish one compact status run at a discontinuity."""
        if self.status_start is not None:
            _event(
                "status_run=", self.status_start, status=self.status,
                observed_through=self.status_end,
                core=self.status_start["telemetry"]["core"],
            )
        self.status_start = None
        self.status = None

    def observe(self, row: dict[str, Any]) -> None:
        """Consume one row, preserving invalid/partial evidence as such."""
        self.rows += 1
        kind = (
            "controller_observation" if row.get("schema") == "eg4_controller_observation.v1"
            else row.get("kind") or "historical"
        )
        if not isinstance(kind, str):
            raise ValueError("record kind must be a string")
        if kind not in self.kinds and len(self.kinds) >= 32:
            kind = "other"
        self.kinds[kind] += 1
        self.errors += row.get("ok") is False
        start, end = _interval(row)
        start_time, end_time = _timestamp(start), _timestamp(end)
        if start_time is not None and (self.first is None or start_time < self.first):
            self.first = start_time
        if end_time is not None and (self.last is None or end_time > self.last):
            self.last = end_time
        _configuration(row)
        session = row.get("session_id") or "unavailable"
        if session != self.previous_session:
            self._close_status()
            self.session_segments += 1
            self.outputs.clear()
            self.controller.clear()
            self.controller_sources.clear()
            self.controller_provenance.clear()
            self.source_interval = None
            if self.previous_session is None:
                _event("session_start", row)
            else:
                gap = (
                    (start_time - self.previous_end).total_seconds()
                    if start_time is not None and self.previous_end is not None else None
                )
                _event(
                    "session_change", row, previous_session=self.previous_session,
                    observation_gap_s=gap, continuity="unknown",
                )
            self.previous_session = session
            self.previous_end = None
        if end_time is not None:
            self.previous_end = max(self.previous_end, end_time) if self.previous_end else end_time

        if kind == "controller_observation":
            _event(
                "controller_observation", row, observation=row.get("observation"),
                changed_fields=row.get("changed_fields"), controller=row.get("controller"),
                availability=row.get("availability"), source_intervals="original",
                physical_meaning="unverified",
            )
            if row.get("ok") is True:
                self._outputs(row, _mapping(row.get("telemetry")))
            return
        if kind == "recorder_health":
            _event("recorder_health", row, observed_at=row.get("observed_at"), health=row.get("health"))
            return
        if kind == "transaction":
            frames = [frame for frame in row.get("frames") or [] if isinstance(frame, dict)]
            for frame in frames:
                rejected = {
                    key: verdict for key, verdict in _mapping(frame.get("validation")).items()
                    if verdict == "failed"
                }
                if rejected:
                    self.raw_rejections += 1
                    _event("raw_rejected", row, validation=rejected, frame=frame)
            partial = bool(row.get("truncated")) or any(
                frame.get("partial") is True or (
                    bool(frame.get("bytes_hex")) and frame.get("values") is None
                    and _mapping(frame.get("validation")).get("framing") != "passed"
                )
                for frame in frames
            )
            failed = row.get("ok") is False
            self.transaction_failures += failed
            self.partial_transactions += partial
            if failed or partial:
                self._close_status()
                _event(
                    "transaction_failed" if failed else "transaction_partial", row,
                    request=row.get("request"), error=row.get("error"),
                    received_bytes=row.get("received_bytes"),
                    truncated=row.get("truncated", False), partial=partial, frames=frames,
                )
            request = _mapping(row.get("request"))
            if (request.get("function") in (3, 4)
                    and type(request.get("register")) is int
                    and type(request.get("count")) is int and request["count"] > 0):
                source = {
                    name: row.get(name) for name in (
                        "acquisition_id", "transaction_id", "started_at", "completed_at",
                    )
                }
                blocks = [
                    {
                        **source, **request, "ok": True, "values": frame.get("values"),
                        "validation": frame.get("validation"), "partial": frame.get("partial"),
                    }
                    for frame in frames if frame.get("classification") == "matching"
                    and frame.get("partial") is not True
                    and not any(
                        value != "passed" for value in _mapping(frame.get("validation")).values()
                    )
                    and isinstance(frame.get("values"), list)
                    and len(frame["values"]) == request["count"]
                ]
                if blocks:
                    raw = {**row, "primary_blocks": blocks}
                    self._controller(raw, availability=False)
                    if request["function"] == 4:
                        self._outputs(raw, {})
            return
        if kind == "interpretation":
            if row.get("semantic") == "rejected":
                self.rejections += 1
                self._close_status()
                _event("semantic_rejected", row, error=row.get("error"))
            return
        if kind not in ("acquisition", "historical"):
            return

        self._source(row)
        self._controller(row)
        self.numbers["gap_s"].add(row.get("sample_gap_seconds"))
        self.numbers["duration_ms"].add(row.get("duration_ms"))
        if row.get("partial"):
            self.acquisition_partials += 1
            _event("acquisition_partial", row, transaction_ids=row.get("transaction_ids"))
        telemetry = _mapping(row.get("telemetry")) if row.get("ok") is True else {}
        self._outputs(row, telemetry)
        if row.get("ok") is not True:
            self.acquisition_failures += 1
            self._close_status()
            _event("acquisition_failed", row, error=row.get("error"))
            return
        if not telemetry:
            self._close_status()
            return
        self.valid += 1
        core = _mapping(telemetry.get("core"))
        if core:
            self.core_samples += 1
            self.numbers["charge_w"].add(core.get("charge_power_w"))
            self.numbers["discharge_w"].add(core.get("discharge_power_w"))
            status = core.get("status_code")
            if not isinstance(status, (int, str)) or isinstance(status, bool):
                status = None
            if status is not None and (status in self.statuses or len(self.statuses) < 256):
                self.statuses[status] += 1
            if status != self.status or status is None:
                self._close_status()
            if status is not None and self.status_start is None:
                # Only one compact decoded observation is retained for the current run.
                self.status_start = {
                    key: row[key] for key in (
                        "role", "session_id", "acquisition_id", "started_at", "completed_at",
                        "sample_started_at", "sample_completed_at",
                    ) if key in row
                }
                self.status_start["telemetry"] = {"core": {
                    key: core.get(key) for key in (
                        "charge_power_w", "grid_voltage_v", "grid_frequency_hz",
                        "eps_voltage_r_v", "eps_frequency_hz",
                    )
                }}
                self.status = status
            self.status_end = end or "unavailable"
        transfer = _mapping(telemetry.get("transfer"))
        if transfer:
            state = transfer.get("state") or "unknown"
            if not isinstance(state, str):
                state = "unknown"
            if state not in self.transfer_states and len(self.transfer_states) >= 32:
                state = "other"
            self.transfer_states[state] += 1
        transition = row.get("transfer_transition")
        if transition:
            self.transitions += 1
            _event(
                "transfer_observation", row, transition=transition,
                decoded=telemetry.get("decoded"),
            )

    def report(self, role: str) -> None:
        """Print coverage, quality and bounded role statistics."""
        self._close_status()
        print(f"[{role}] rows={self.rows} valid={self.valid} errors={self.errors}")
        print(f"  kinds={dict(sorted(self.kinds.items()))}")
        print(
            f"  coverage_start={self.first.isoformat() if self.first else 'unavailable'} "
            f"coverage_end={self.last.isoformat() if self.last else 'unavailable'}"
        )
        print(
            f"  transaction_failures={self.transaction_failures} "
            f"partial_transactions={self.partial_transactions} "
            f"semantic_rejections={self.rejections}"
        )
        print(f"  raw_rejections={self.raw_rejections}")
        print(
            f"  acquisition_failures={self.acquisition_failures} "
            f"acquisition_partials={self.acquisition_partials} acquisition_gaps={self.acquisition_gaps} "
            f"session_segments={self.session_segments}"
        )
        print(
            f"  source_coverage_start={self.source_first.isoformat() if self.source_first else 'unavailable'} "
            f"source_coverage_end={self.source_last.isoformat() if self.source_last else 'unavailable'}"
        )
        if self.kinds["historical"] or self.kinds["acquisition"]:
            self.numbers["gap_s"].report("gap_s")
            self.numbers["uncovered_s"].report("uncovered_s")
            self.numbers["duration_ms"].report("duration_ms")
        if self.core_samples:
            print(f"  status_counts={dict(self.statuses)}")
            self.numbers["charge_w"].report("charge_w")
            self.numbers["discharge_w"].report("discharge_w")
        if self.transfer_states:
            print(f"  transfer_states={dict(self.transfer_states)}")
            print(f"  transitions={self.transitions}")


@dataclass
class Analysis:
    """Keep only bounded per-role summaries; emit events as rows arrive."""

    roles: list[str] | None = None
    gap_seconds: float = 5
    summaries: dict[str, RoleSummary] = field(default_factory=dict)
    coverage: Coverage | None = None
    summary_identities: dict[str, tuple[Any, Any, str | None]] = field(default_factory=dict)

    def consume(self, path: Path) -> int:
        """Analyze a single retained stream without retaining its rows."""
        print(f"source_file={path}")
        triggers = 0
        for row in _evidence_rows(path):
            if self.coverage is not None:
                self.coverage.observe(row, str(path))
            triggers += row.get("kind") == "incident_trigger"
            if row.get("kind") == "incident_context":
                # Like the manifest's first snapshot, this is all-role context,
                # not another role acquisition or a new unknown-role session.
                _configuration(row)
                continue
            role = row.get("role") or "unknown"
            if not isinstance(role, str):
                raise ValueError(f"{path}: role must be a string")
            if self.roles and role not in self.roles:
                continue
            if row.get("kind") == "incident_trigger":
                _event(
                    "incident_trigger", row, reason=row.get("reason"),
                    observed_at=row.get("observed_at"), details=row.get("details"),
                    incident_id=row.get("incident_id"),
                )
                continue
            identity = (row.get("build_id"), row.get("session_id"),
                        None if row.get("session_id") else str(path))
            if role in self.summaries and self.summary_identities[role] != identity:
                previous = self.summaries[role]
                self._report_role(role)
                self.summaries[role] = RoleSummary(
                    gap_seconds=self.gap_seconds, previous_session=previous.previous_session,
                    previous_end=previous.previous_end,
                )
                _event("source_identity_change", row, previous_identity=self.summary_identities[role],
                       current_identity=identity, continuity="unknown")
            if role not in self.summaries:
                if len(self.summaries) >= MAX_ROLES:
                    raise ValueError(f"{path}: role limit exceeded; analysis incomplete")
                self.summaries[role] = RoleSummary(gap_seconds=self.gap_seconds)
            self.summary_identities[role] = identity
            self.summaries[role].observe(row)
        return triggers

    def consume_retained(self, path: Path) -> bool:
        """Keep safe directory files visible after an explicitly reported gap."""
        try:
            self.consume(path)
        except (OSError, ValueError) as error:
            print("  input_file_gap=" + json.dumps({
                "path": str(path), "error": str(error),
                "continuity": "unknown", "analysis_complete": False,
            }, sort_keys=True))
            if self.coverage is not None:
                self.coverage.fact({}, str(path), "input_file_gap", {"path": str(path), "error": str(error)})
            for summary in self.summaries.values():
                summary._close_status()
                summary.source_interval = None
            return False
        return True

    def report(self, prefix: str = "") -> None:
        """Finish each independent source group without combining copied evidence."""
        for role in sorted(self.summaries):
            self._report_role(role, prefix)

    def _report_role(self, role: str, prefix: str = "") -> None:
        build, session, anonymous_file = self.summary_identities[role]
        print("  summary_identity=" + json.dumps({
            "build_id": build, "session_id": session, "role": role,
            "anonymous_source_file": anonymous_file, "scope": "contiguous_source_segment",
        }, sort_keys=True))
        self.summaries[role].report(prefix + role)


def _file_integrity(path: Path, metadata: dict[str, Any]) -> tuple[bool, str]:
    """Verify final hashes in chunks; growing files never receive final custody."""
    with _open_evidence(path) as source:
        before = os.fstat(source.fileno())
        if metadata.get("finalized") is not True:
            return False, (
                "sha256=invalid_growing_hash" if metadata.get("sha256")
                else "sha256=unavailable_growing"
            )
        digest = hashlib.sha256()
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        return False, "sha256=unresolved_file_changed"
    size_ok = type(metadata.get("bytes")) is int and metadata["bytes"] == after.st_size
    expected = metadata.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        return False, f"sha256=missing_final_hash bytes_match={str(size_ok).lower()}"
    hash_ok = digest.hexdigest() == expected.lower()
    return hash_ok and size_ok, (
        f"sha256={'verified' if hash_ok else 'mismatch'} "
        f"bytes_match={str(size_ok).lower()} actual_bytes={after.st_size}"
    )


def _gaps_affect_coverage(manifest: dict[str, Any]) -> bool:
    """Exempt only a located startup boundary strictly before captured coverage."""
    gaps = manifest.get("gaps")
    if not isinstance(gaps, list):
        print("  invalid_manifest_gaps")
        return True
    coverage = _mapping(manifest.get("coverage"))
    start = _timestamp(coverage.get("actual_start"))
    end = _timestamp(coverage.get("actual_end"))
    pre_start = _timestamp(coverage.get("pre_start"))
    located = (
        start is not None and end is not None and end >= start
        and (coverage.get("pre_start") is None or pre_start is not None)
    )
    if start is not None and pre_start is not None:
        start = min(start, pre_start)
    affects_coverage = False
    for gap in gaps:
        metadata = _mapping(gap)
        observed_at = _timestamp(metadata.get("observed_at"))
        before_coverage = (
            located and start is not None and observed_at is not None
            and observed_at < start
            and metadata.get("reason") == "new_recorder_session"
            and isinstance(metadata.get("session_id"), str) and bool(metadata["session_id"])
            and metadata["session_id"] == manifest.get("session_id")
            and metadata.get("prebuffer_warmup") is True
        )
        affects_coverage |= not before_coverage
        print("  gap_assessment=" + json.dumps({
            "gap": gap, "affects_captured_coverage": not before_coverage,
        }, sort_keys=True))
    return affects_coverage


def _incident(root: Path, analysis: Analysis) -> None:
    """Verify and analyze one bundle separately from rolling acquisitions."""
    print(f"source_group=incident:{root.name} incident_directory={root}")
    try:
        _safe_path(root)
        manifest = _json_document(root / "manifest.json")
    except (OSError, ValueError) as error:
        print(f"  manifest_verification=incomplete error={error}")
        return
    required = {
        "schema", "incident_id", "session_id", "build_id", "entry_id", "evidence_class",
        "state", "started_at", "last_trigger_at", "closed_at", "expected_roles",
        "observed_roles", "missing_roles", "complete", "truncated", "incomplete_reasons",
        "errors", "gaps", "coverage", "configuration_context", "triggers",
        "trigger_count", "trigger_files", "files",
    }
    missing_keys = sorted(required - manifest.keys())
    complete = (
        not missing_keys and manifest.get("schema") == "eg4_host_incident.v1"
        and manifest.get("state") == "closed" and manifest.get("complete") is True
        and manifest.get("truncated") is False and not manifest.get("missing_roles")
        and not manifest.get("incomplete_reasons") and not manifest.get("errors")
    )
    print("  manifest=" + json.dumps({
        key: manifest.get(key) for key in (
            "schema", "incident_id", "session_id", "build_id", "entry_id", "evidence_class",
            "state", "started_at", "last_trigger_at", "closed_at",
            "expected_roles", "observed_roles", "missing_roles", "complete", "truncated",
            "incomplete_reasons", "errors", "gaps", "coverage", "trigger_count", "triggers",
        )
    }, sort_keys=True))
    gap_limits_coverage = _gaps_affect_coverage(manifest)
    if analysis.coverage is not None:
        analysis.coverage.metadata({
            **manifest, "gaps": manifest.get("gaps") if gap_limits_coverage else [],
        }, str(root / "manifest.json"))
        if manifest.get("gaps") and not gap_limits_coverage:
            analysis.coverage.fact(manifest, str(root / "manifest.json"),
                                   "gaps_outside_captured_coverage", manifest["gaps"])
    if missing_keys:
        print(f"  missing_manifest_fields={json.dumps(missing_keys)}")
    if gap_limits_coverage:
        complete = False
    _configuration(manifest)
    if manifest.get("state") == "closed" and _timestamp(manifest.get("closed_at")) is None:
        complete = False
        print("  missing_closed_at")
    if not isinstance(manifest.get("configuration_context"), dict):
        complete = False
        print("  invalid_configuration_context")
    if not all(
        isinstance(manifest.get(key), str) and manifest[key]
        for key in ("incident_id", "session_id", "build_id", "entry_id", "evidence_class")
    ):
        complete = False
        print("  invalid_manifest_identity")
    coverage = _mapping(manifest.get("coverage"))
    required_coverage = {
        "requested_pre_seconds", "requested_post_seconds", "actual_start", "actual_end",
        "pre_start", "pre_end", "warmup", "memory_truncated",
    }
    if required_coverage - coverage.keys():
        complete = False
        print("  missing_coverage_fields=" + json.dumps(sorted(required_coverage - coverage.keys())))
    if coverage.get("warmup") or coverage.get("memory_truncated"):
        complete = False
    expected, observed, missing = (
        manifest.get("expected_roles"), manifest.get("observed_roles"), manifest.get("missing_roles")
    )
    if (
        not isinstance(expected, list) or not isinstance(observed, list)
        or not isinstance(missing, list)
        or not all(isinstance(role, str) for group in (expected, observed, missing) for role in group)
    ):
        complete = False
        print("  invalid_role_coverage")
    elif set(expected) - set(observed) != set(missing):
        complete = False
        print("  inconsistent_missing_roles")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > MAX_DIRECTORY_ENTRIES:
        print("  manifest_verification=incomplete error=invalid_or_excessive_files")
        return
    seen = set()
    trigger_paths = set()
    trigger_count = 0
    if not entries:
        complete = False
        print("  missing_retained_evidence")
    retained_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            complete = False
            print("  invalid_file_entry")
            continue
        relative = Path(entry["path"])
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            complete = False
            print(f"  unsafe_path={json.dumps(entry['path'])}")
            continue
        name = relative.as_posix()
        if name in seen:
            complete = False
            print(f"  duplicate_file_reference={json.dumps(name)}")
            continue
        seen.add(name)
        if entry.get("kind") == "triggers":
            trigger_paths.add(name)
        if entry.get("retained") is not True:
            complete = False
            if entry.get("retained") is False:
                print(f"  lost_file={json.dumps(name)} retained=false")
            else:
                print(f"  invalid_retention_flag={json.dumps(name)}")
            continue
        if entry.get("kind") not in ("records", "triggers"):
            complete = False
            print(f"  unknown_file_kind={json.dumps(entry.get('kind'))}")
            continue
        retained_entries.append((entry["kind"], relative, entry))
    retained_entries.sort(key=lambda item: (
        item[0], item[1].parent.as_posix(), *_segment_order(item[1]),
    ))
    for _, relative, entry in retained_entries:
        name = relative.as_posix()
        try:
            path = _safe_path(root / relative, root)
            verified, status = _file_integrity(path, entry)
            complete = complete and verified
            print(f"  manifest_file={json.dumps(name)} {status}")
            trigger_count += analysis.consume(path)
        except FileNotFoundError:
            complete = False
            print(f"  missing_file={json.dumps(name)}")
        except (OSError, ValueError) as error:
            complete = False
            print(f"  manifest_file={json.dumps(name)} error={error}")
    if type(manifest.get("trigger_count")) is not int or manifest["trigger_count"] != trigger_count:
        complete = False
        print(f"  trigger_count_mismatch retained_trigger_count={trigger_count}")
    references = manifest.get("trigger_files")
    if not isinstance(references, list) or not all(isinstance(path, str) for path in references):
        complete = False
        print("  invalid_trigger_files")
    elif set(references) != trigger_paths:
        complete = False
        print("  missing_or_inconsistent_trigger_file_references")
    print(f"  manifest_verification={'complete' if complete else 'incomplete'}")
    if not complete and analysis.coverage is not None:
        analysis.coverage.fact(manifest, str(root / "manifest.json"), "manifest_custody_failure",
                               {"manifest_verification": "incomplete", "sample_coverage": "independent"})


def _analyze_path(
    path: Path, roles: list[str] | None, gap_seconds: float, *, registers: bool = False,
) -> None:
    """Keep incident custody independent; deduplicate rolling and journal copies."""
    path = _safe_path(path)
    with ExitStack() as resources:
        def coverage() -> Coverage:
            return resources.enter_context(Coverage(registers=registers, gap_seconds=gap_seconds, roles=roles))

        rolling = coverage()
        analysis = Analysis(roles=roles, gap_seconds=gap_seconds, coverage=rolling)
        if path.is_file():
            analysis.consume(path)
            analysis.report()
            rolling.report("file")
            return
        if not path.is_dir():
            raise ValueError(f"{path}: expected an evidence file or directory")
        if (path / "manifest.json").exists():
            _incident(path, analysis)
            analysis.report()
            rolling.report(f"incident:{path.name}")
            return
        marker = path / "recorder.json"
        if marker.exists():
            metadata = _json_document(marker)
            if metadata.get("schema") != "eg4_host_recorder.v1":
                raise ValueError(f"{marker}: unsupported recorder schema")
            print(f"recorder={json.dumps(metadata, sort_keys=True)}")
            rolling.metadata(metadata, str(marker))
        files = 0
        damaged_files = 0
        print("source_group=rolling incident_copies_excluded=true")
        for trace in _trace_files(path, historical=True):
            damaged_files += not analysis.consume_retained(trace)
            files += 1
        for baseline in _children(path):
            if baseline.suffix == ".json" and "baseline" in baseline.name:
                damaged_files += not analysis.consume_retained(baseline)
                files += 1
        for trace in _trace_files(path / "raw"):
            damaged_files += not analysis.consume_retained(trace)
            files += 1
        analysis.report()
        journal = Analysis(roles=roles, gap_seconds=gap_seconds, coverage=rolling)
        print("source_group=journal separate_controller_observations=true")
        for trace in _trace_files(path / "controller-observations"):
            damaged_files += not journal.consume_retained(trace)
            files += 1
        journal.report("journal:")
        rolling.report("rolling_and_journal_deduplicated")
        incident_root = path / "incidents"
        if incident_root.exists():
            for bundle in _children(incident_root):
                if bundle.name.startswith("incident-"):
                    retained = coverage()
                    incident_analysis = Analysis(roles=roles, gap_seconds=gap_seconds, coverage=retained)
                    _incident(bundle, incident_analysis)
                    incident_analysis.report(f"incident:{bundle.name}:")
                    retained.report(f"incident:{bundle.name}")
                    files += 1
        if not files:
            raise ValueError(f"{path}: no recognizable evidence files or incident manifests")
        if damaged_files:
            raise ValueError(
                f"{path}: directory analysis incomplete: {damaged_files} damaged or unreadable files; "
                "later safe files were analyzed without assuming continuity"
            )


def main() -> int:
    """Run the read-only, standard-library evidence analyzer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="JSONL, retained baseline JSON, or recorder/incident directory (read-only)")
    parser.add_argument(
        "--role", action="append",
        help="include only this role's observations; all-role bundle context remains visible; repeatable",
    )
    parser.add_argument(
        "--gap-seconds", type=float, default=5,
        help="report uncovered acquisition intervals longer than this many seconds (default: 5)",
    )
    parser.add_argument("--registers", action="store_true",
                        help="emit source-bound canonical register projections, including raw/uncertain fields")
    args = parser.parse_args()
    if not math.isfinite(args.gap_seconds) or args.gap_seconds < 0:
        parser.error("--gap-seconds must be finite and nonnegative")
    print("Evidence observations only; register projections retain source quality and calibration uncertainty.")
    print("Acquisition intervals are host observations, not engine state or physical contacts.")
    print("Onset/recovery transition times and overlapping interval ordering remain unresolved.")
    print("No zero-fill, interpolation or continuous house-outage duration is inferred.")
    try:
        _analyze_path(args.trace, args.role, args.gap_seconds, registers=args.registers)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
