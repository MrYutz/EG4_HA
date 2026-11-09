#!/usr/bin/env python3
"""Stream a historical or raw-evidence EG4 local-dongle JSONL file."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _interval(row: dict[str, Any]) -> tuple[Any, Any]:
    return (
        row.get("started_at") or row.get("sample_started_at"),
        row.get("completed_at") or row.get("sample_completed_at"),
    )


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _context(row: dict[str, Any]) -> str:
    start, end = _interval(row)
    return (
        f"role={row.get('role', 'unknown')} "
        f"session={row.get('session_id') or 'unavailable'} "
        f"acquisition={row.get('acquisition_id') or 'unavailable'} "
        f"transaction={row.get('transaction_id') or 'unavailable'} "
        f"start={start or 'unavailable'} end={end or 'unavailable'}"
    )


def _event(name: str, row: dict[str, Any], **details: Any) -> None:
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
        if self.count:
            print(
                f"  {name}=min:{self.minimum:.3f} avg:{self.total / self.count:.3f} "
                f"max:{self.maximum:.3f} latest:{self.latest:.3f} n:{self.count}"
            )
        else:
            print(f"  {name}=unavailable n:0")


@dataclass
class RoleSummary:
    rows: int = 0
    valid: int = 0
    errors: int = 0
    core_samples: int = 0
    transaction_failures: int = 0
    partial_transactions: int = 0
    rejections: int = 0
    transitions: int = 0
    kinds: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    transfer_states: Counter = field(default_factory=Counter)
    numbers: dict[str, NumericSummary] = field(default_factory=lambda: {
        name: NumericSummary()
        for name in ("gap_s", "duration_ms", "charge_w", "discharge_w")
    })
    first: datetime | None = None
    last: datetime | None = None
    previous_session: str | None = None
    previous_end: datetime | None = None
    status: Any = None
    status_start: dict[str, Any] | None = None
    status_end: Any = None

    def _close_status(self) -> None:
        if self.status_start is not None:
            _event(
                "status_run=", self.status_start, status=self.status,
                observed_through=self.status_end,
                core=self.status_start["telemetry"]["core"],
            )
        self.status_start = None
        self.status = None

    def observe(self, row: dict[str, Any]) -> None:
        self.rows += 1
        kind = row.get("kind") or "historical"
        self.kinds[kind] += 1
        self.errors += row.get("ok") is False
        start, end = _interval(row)
        start_time, end_time = _timestamp(start), _timestamp(end)
        if start_time is not None and (self.first is None or start_time < self.first):
            self.first = start_time
        if end_time is not None and (self.last is None or end_time > self.last):
            self.last = end_time
        session = row.get("session_id") or "unavailable"
        if session != self.previous_session:
            self._close_status()
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

        if kind == "transaction":
            frames = row.get("frames") or []
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
            return
        if kind == "interpretation":
            if row.get("semantic") == "rejected":
                self.rejections += 1
                self._close_status()
                _event("semantic_rejected", row, error=row.get("error"))
            return
        if kind not in ("acquisition", "historical"):
            return

        self.numbers["gap_s"].add(row.get("sample_gap_seconds"))
        self.numbers["duration_ms"].add(row.get("duration_ms"))
        if row.get("partial"):
            _event("acquisition_partial", row, transaction_ids=row.get("transaction_ids"))
        if row.get("ok") is not True:
            self._close_status()
            _event("acquisition_failed", row, error=row.get("error"))
            return
        telemetry = _mapping(row.get("telemetry"))
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
            if status is not None:
                self.statuses[status] += 1
            if status != self.status or status is None:
                self._close_status()
            if status is not None and self.status_start is None:
                # Only one compact decoded observation is retained for the current run.
                self.status_start = {
                    key: row[key] for key in (
                        "role", "session_id", "acquisition_id", "started_at", "completed_at",
                        "sample_started_at", "sample_completed_at", "written_at",
                    ) if key in row
                }
                self.status_start["telemetry"] = {"core": {
                    key: core.get(key) for key in (
                        "charge_power_w", "grid_voltage_v", "grid_frequency_hz",
                        "eps_voltage_r_v", "eps_frequency_hz",
                    )
                }}
                self.status = status
            self.status_end = end or row.get("written_at") or "unavailable"
        transfer = _mapping(telemetry.get("transfer"))
        if transfer:
            self.transfer_states[transfer.get("state") or "unknown"] += 1
        transition = row.get("transfer_transition")
        if transition:
            self.transitions += 1
            _event(
                "transfer_observation", row, transition=transition,
                decoded=telemetry.get("decoded"),
            )

    def report(self, role: str) -> None:
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
        if self.kinds["historical"] or self.kinds["acquisition"]:
            self.numbers["gap_s"].report("gap_s")
            self.numbers["duration_ms"].report("duration_ms")
        if self.core_samples:
            print(f"  status_counts={dict(self.statuses)}")
            self.numbers["charge_w"].report("charge_w")
            self.numbers["discharge_w"].report("discharge_w")
        if self.transfer_states:
            print(f"  transfer_states={dict(self.transfer_states)}")
            print(f"  transitions={self.transitions}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="single JSONL evidence file (read-only)")
    parser.add_argument("--role", action="append", help="include only this role; repeatable")
    args = parser.parse_args()
    summaries: dict[str, RoleSummary] = {}
    print("Evidence observations only; raw/rejected words are not decoded telemetry.")
    print("Acquisition intervals are host observations, not engine state or physical contacts.")
    with args.trace.open(encoding="utf-8") as trace:
        for line_number, line in enumerate(trace, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                parser.exit(2, f"{args.trace}:{line_number}: invalid JSON: {error.msg}\n")
            if not isinstance(row, dict):
                parser.exit(2, f"{args.trace}:{line_number}: expected a JSON object\n")
            role = row.get("role") or "unknown"
            if args.role and role not in args.role:
                continue
            if role not in summaries:
                summaries[role] = RoleSummary()
            summaries[role].observe(row)
    for role in sorted(summaries):
        summaries[role].report(role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
