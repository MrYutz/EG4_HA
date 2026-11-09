#!/usr/bin/env python3
"""Summarize an EG4 local-dongle JSONL trace."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def _values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get("telemetry", {}).get("core", {}).get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _print_gridboss(rows: list[dict[str, Any]]) -> None:
    """Summarize direct GridBOSS source and transfer observations."""
    valid = [row for row in rows if row.get("ok") and row.get("telemetry")]
    errors = [row for row in rows if not row.get("ok")]
    print(f"[gridboss] rows={len(rows)} valid={len(valid)} errors={len(errors)}")
    if not valid:
        return
    states = Counter(
        row["telemetry"].get("transfer", {}).get("state", "unknown")
        for row in valid
    )
    transitions = [
        row["transfer_transition"]
        for row in valid
        if row.get("transfer_transition")
    ]
    print(f"  transfer_states={dict(states)}")
    print(f"  transitions={len(transitions)}")
    for row in valid:
        transition = row.get("transfer_transition")
        if transition:
            decoded = row["telemetry"].get("decoded", {})
            print(
                "  transition="
                f"{row.get('written_at')} {transition.get('from')} -> "
                f"{transition.get('to')} grid_v:{decoded.get('grid_voltage_v')} "
                f"grid_hz:{decoded.get('grid_frequency_hz')} "
                f"gen_v:{decoded.get('generator_voltage_v')} "
                f"gen_hz:{decoded.get('generator_frequency_hz')} "
                f"gen_w:{decoded.get('generator_power_w')} "
                f"ups_v:{decoded.get('ups_voltage_v')} "
                f"ups_w:{decoded.get('ups_power_w')}"
            )


def _print_role(role: str, rows: list[dict[str, Any]]) -> None:
    valid = [row for row in rows if row.get("ok") and row.get("telemetry")]
    errors = [row for row in rows if not row.get("ok")]
    print(f"[{role}] rows={len(rows)} valid={len(valid)} errors={len(errors)}")
    if not valid:
        return

    gaps = [
        float(row["sample_gap_seconds"])
        for row in rows
        if isinstance(row.get("sample_gap_seconds"), (int, float))
    ]
    durations = [
        float(row["duration_ms"])
        for row in rows
        if isinstance(row.get("duration_ms"), (int, float))
    ]
    charge = _values(valid, "charge_power_w")
    discharge = _values(valid, "discharge_power_w")
    statuses = Counter(
        row["telemetry"]["core"].get("status_code") for row in valid
    )
    if gaps:
        print(
            "  gap_s="
            f"min:{min(gaps):.3f} median:{statistics.median(gaps):.3f} "
            f"max:{max(gaps):.3f}"
        )
    if durations:
        print(
            "  duration_ms="
            f"min:{min(durations):.1f} median:{statistics.median(durations):.1f} "
            f"max:{max(durations):.1f}"
        )
    print(f"  status_counts={dict(statuses)}")
    print(
        "  charge_w="
        f"min:{min(charge):.0f} avg:{statistics.mean(charge):.0f} "
        f"max:{max(charge):.0f} latest:{charge[-1]:.0f}"
    )
    print(
        "  discharge_w="
        f"min:{min(discharge):.0f} avg:{statistics.mean(discharge):.0f} "
        f"max:{max(discharge):.0f} latest:{discharge[-1]:.0f}"
    )

    previous_status: object = object()
    run_start: dict[str, Any] | None = None
    for row in valid + [None]:
        status = (
            row["telemetry"]["core"].get("status_code") if row is not None else None
        )
        if run_start is None:
            run_start = row
            previous_status = status
            continue
        if status == previous_status:
            continue
        core = run_start["telemetry"]["core"]
        print(
            "  status_run="
            f"{previous_status} start:{run_start.get('written_at')} "
            f"end:{row.get('written_at') if row else valid[-1].get('written_at')} "
            f"charge_w:{core.get('charge_power_w')} "
            f"grid_v:{core.get('grid_voltage_v')} "
            f"grid_hz:{core.get('grid_frequency_hz')} "
            f"eps_v:{core.get('eps_voltage_r_v')} "
            f"eps_hz:{core.get('eps_frequency_hz')}"
        )
        run_start = row
        previous_status = status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--role", action="append")
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    roles = args.role or sorted({row.get("role") for row in rows if row.get("role")})
    for role in roles:
        matching = [row for row in rows if row.get("role") == role]
        if role == "gridboss":
            _print_gridboss(matching)
        else:
            _print_role(role, matching)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
