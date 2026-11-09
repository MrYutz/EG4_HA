"""Rolling grid quality calculations for EG4 Monitor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

GRID_QUALITY_WINDOW = timedelta(hours=24)
OUTAGE_VOLTAGE_THRESHOLD = 200.0
BROWNOUT_VOLTAGE_THRESHOLD = 216.0
DEFAULT_MAX_SAMPLE_GAP_SECONDS = 600

GRID_STATE_NORMAL = "normal"
GRID_STATE_BROWNOUT = "brownout"
GRID_STATE_OUTAGE = "outage"
GRID_STATE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GridQualitySample:
    """One grid voltage sample for rolling quality calculations."""

    ts: datetime
    voltage: float | None
    state: str


def classify_grid_voltage(voltage: Any) -> str:
    """Classify grid quality from line voltage."""
    try:
        voltage_f = float(voltage)
    except (TypeError, ValueError):
        return GRID_STATE_UNAVAILABLE

    if voltage_f < OUTAGE_VOLTAGE_THRESHOLD:
        return GRID_STATE_OUTAGE
    if voltage_f < BROWNOUT_VOLTAGE_THRESHOLD:
        return GRID_STATE_BROWNOUT
    return GRID_STATE_NORMAL


def build_grid_quality_snapshot(
    samples: list[GridQualitySample],
    now: datetime,
    max_sample_gap_seconds: int = DEFAULT_MAX_SAMPLE_GAP_SECONDS,
) -> dict[str, Any]:
    """Build rolling 24-hour quality metrics from ordered samples."""
    window_start = now - GRID_QUALITY_WINDOW
    samples[:] = [sample for sample in samples if sample.ts >= window_start]

    outage_seconds = 0.0
    brownout_starts = 0
    outage_starts = 0

    previous_state = GRID_STATE_NORMAL
    for sample in samples:
        if sample.state == GRID_STATE_BROWNOUT and previous_state != GRID_STATE_BROWNOUT:
            brownout_starts += 1
        if sample.state == GRID_STATE_OUTAGE and previous_state != GRID_STATE_OUTAGE:
            outage_starts += 1
        if sample.state != GRID_STATE_UNAVAILABLE:
            previous_state = sample.state

    for previous, current in zip(samples, samples[1:], strict=False):
        delta = (current.ts - previous.ts).total_seconds()
        if delta <= 0 or delta > max_sample_gap_seconds:
            continue
        if previous.state == GRID_STATE_OUTAGE:
            outage_seconds += delta

    current_sample = samples[-1] if samples else None
    return {
        "State": current_sample.state if current_sample else GRID_STATE_UNAVAILABLE,
        "Voltage": current_sample.voltage if current_sample else None,
        "OutageDurationMinutes24h": round(outage_seconds / 60.0, 2),
        "OutageCount24h": outage_starts,
        "BrownoutCount24h": brownout_starts,
        "WindowHours": 24,
        "SampleCount": len(samples),
        "MaxSampleGapSeconds": max_sample_gap_seconds,
        "OutageVoltageThreshold": OUTAGE_VOLTAGE_THRESHOLD,
        "BrownoutVoltageThreshold": BROWNOUT_VOLTAGE_THRESHOLD,
        "WindowStart": window_start.isoformat(),
    }


def append_grid_quality_sample(
    samples: list[GridQualitySample],
    now: datetime,
    voltage: Any,
    max_sample_gap_seconds: int = DEFAULT_MAX_SAMPLE_GAP_SECONDS,
) -> dict[str, Any]:
    """Append a voltage sample and return a fresh rolling snapshot."""
    try:
        voltage_f = float(voltage)
    except (TypeError, ValueError):
        voltage_f = None

    samples.append(
        GridQualitySample(
            ts=now,
            voltage=voltage_f,
            state=classify_grid_voltage(voltage_f),
        )
    )
    return build_grid_quality_snapshot(samples, now, max_sample_gap_seconds)


def serialize_grid_quality_samples(
    samples: list[GridQualitySample],
) -> list[dict[str, Any]]:
    """Serialize grid quality samples for Home Assistant storage."""
    return [
        {
            "ts": sample.ts.isoformat(),
            "voltage": sample.voltage,
            "state": sample.state,
        }
        for sample in samples
    ]


def deserialize_grid_quality_samples(
    raw_samples: Any,
    now: datetime,
) -> list[GridQualitySample]:
    """Deserialize recent grid quality samples from Home Assistant storage."""
    if not isinstance(raw_samples, list):
        return []

    window_start = now - GRID_QUALITY_WINDOW
    samples: list[GridQualitySample] = []
    for item in raw_samples:
        if not isinstance(item, dict):
            continue
        try:
            ts = datetime.fromisoformat(str(item.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None and now.tzinfo is not None:
            ts = ts.replace(tzinfo=now.tzinfo)
        elif ts.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=ts.tzinfo)
            window_start = now - GRID_QUALITY_WINDOW
        if ts < window_start:
            continue

        voltage = item.get("voltage")
        try:
            voltage_f = None if voltage is None else float(voltage)
        except (TypeError, ValueError):
            voltage_f = None
        state = str(item.get("state") or classify_grid_voltage(voltage_f))
        samples.append(GridQualitySample(ts=ts, voltage=voltage_f, state=state))

    samples.sort(key=lambda sample: sample.ts)
    return samples
