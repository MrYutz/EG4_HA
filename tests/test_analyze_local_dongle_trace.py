"""Exercise the offline analyzer through its actual single-file CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ANALYZER = Path(__file__).resolve().parents[1] / "scripts/analyze_local_dongle_trace.py"


def _analyze(tmp_path, rows, *arguments):
    trace = tmp_path / "synthetic-evidence.jsonl"
    original = "\n".join(json.dumps(row) for row in rows) + "\n"
    trace.write_text(original, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ANALYZER), str(trace), *arguments],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert trace.read_text(encoding="utf-8") == original
    return result.stdout


def test_cli_reports_rejected_and_partial_raw_evidence(tmp_path):
    start = "2026-11-09T12:00:00+00:00"
    end = "2026-11-09T12:00:01+00:00"
    common = {
        "schema": "eg4_local_dongle_trace.v2", "role": "master",
        "session_id": "synthetic-session-a", "acquisition_id": "acq-rejected",
        "started_at": start, "completed_at": end,
        "written_at": "2026-11-09T18:00:00+00:00",
    }
    output = _analyze(tmp_path, [
        {
            **common, "kind": "transaction", "transaction_id": "tx-rejected",
            "ok": True, "received_bytes": 4, "truncated": False,
            "request": {"function": 4, "register": 0, "count": 2},
            "frames": [{"bytes_hex": "00820000", "values": [33280, 0],
                        "validation": {"framing": "passed", "crc": "passed"}}],
        },
        {
            **common, "kind": "interpretation", "transaction_id": "tx-rejected",
            "semantic": "rejected", "error": {
                "type": "DongleFrameError", "stage": "decode",
                "message": "invalid inverter status: 33280",
            },
        },
        {
            **common, "kind": "transaction", "role": "gridboss",
            "acquisition_id": "acq-partial", "transaction_id": "tx-partial",
            "ok": False, "received_bytes": 3, "truncated": False,
            "request": {"function": 4, "register": 120, "count": 12},
            "error": {"type": "TimeoutError", "stage": "read", "message": "timeout"},
            "frames": [{"bytes_hex": "a11a02", "values": None,
                        "validation": {"framing": "not_evaluated"}}],
        },
        {
            **common, "kind": "acquisition", "role": "gridboss", "ok": False,
            "partial": True, "acquisition_id": "acq-partial",
            "transaction_ids": ["tx-power", "tx-partial"],
        },
    ])
    assert "[master] rows=2" in output
    assert "kinds={'interpretation': 1, 'transaction': 1}" in output
    assert "semantic_rejections=1" in output
    assert "semantic_rejected" in output
    assert "invalid inverter status: 33280" in output
    assert "transaction=tx-rejected" in output
    assert f"start={start} end={end}" in output
    assert "session=synthetic-session-a" in output
    assert "transaction_failed" in output
    assert "transaction=tx-partial" in output
    assert '"bytes_hex": "a11a02"' in output
    assert '"stage": "read"' in output
    assert "transaction_failures=1 partial_transactions=1" in output
    assert "acquisition_partial" in output
    assert "charge_w=" not in output
    assert "status_run=" not in output
    assert "18:00:00" not in output


def test_cli_retains_historical_status_and_transfer_observations(tmp_path):
    output = _analyze(tmp_path, [
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "master", "ok": True,
            "sample_started_at": "2026-11-08T10:00:00+00:00",
            "sample_completed_at": "2026-11-08T10:00:01+00:00",
            "duration_ms": 1000, "telemetry": {
                "core": {"status_code": 32, "charge_power_w": 750,
                         "discharge_power_w": None},
            },
        },
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "master", "ok": True,
            "sample_started_at": "2026-11-08T10:00:02+00:00",
            "sample_completed_at": "2026-11-08T10:00:03+00:00",
            "sample_gap_seconds": 2, "telemetry": {
                "core": {"status_code": 17, "charge_power_w": None},
            },
        },
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "gridboss", "ok": True,
            "sample_started_at": "2026-11-08T10:00:04+00:00",
            "sample_completed_at": "2026-11-08T10:00:05+00:00",
            "telemetry": {
                "decoded": {"generator_voltage_v": 239, "generator_frequency_hz": 0},
                "transfer": {"state": "grid"},
            },
            "transfer_transition": {"from": "generator", "to": "grid"},
        },
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "slave", "ok": True,
            "telemetry": {"core": {"status_code": 32}},
        },
    ])
    assert "kinds={'historical': 2}" in output
    assert "status_counts={32: 1, 17: 1}" in output
    assert "charge_w=min:750.000 avg:750.000 max:750.000 latest:750.000 n:1" in output
    assert "discharge_w=unavailable n:0" in output
    assert "status_run=" in output
    assert 'status": 32' in output
    assert "session=unavailable" in output
    assert "start=2026-11-08T10:00:00+00:00 end=2026-11-08T10:00:01+00:00" in output
    assert "coverage_end=2026-11-08T10:00:03+00:00" in output
    assert "transfer_states={'grid': 1}" in output
    assert "transfer_observation" in output
    assert '"generator_voltage_v": 239' in output
    assert '"generator_frequency_hz": 0' in output
    assert '"from": "generator", "to": "grid"' in output
    assert "mechanical_stop" not in output


def test_cli_filters_roles_and_reports_session_gap_without_filling_missing_data(tmp_path):
    output = _analyze(tmp_path, [
        {
            "kind": "transaction", "role": "master", "session_id": "session-before",
            "started_at": "2026-11-09T10:00:00+00:00",
            "completed_at": "2026-11-09T10:00:01+00:00",
            "ok": True, "frames": [],
        },
        {
            "kind": "transaction", "role": "slave", "session_id": "excluded-session",
            "ok": False, "frames": [], "error": {"message": "excluded-error"},
        },
        {
            "kind": "interpretation", "role": "master", "session_id": "session-after",
            "started_at": "2026-11-09T10:00:11+00:00",
            "completed_at": "2026-11-09T10:00:12+00:00",
            "semantic": "rejected", "error": {"message": "inconsistent voltage/frequency"},
        },
        {
            "kind": "interpretation", "role": "gridboss",
            "session_id": "session-gridboss", "semantic": "rejected",
            "transaction_id": "tx-without-time", "error": {"message": "missing time"},
        },
    ], "--role", "master", "--role", "gridboss")
    assert "[master] rows=2" in output
    assert "[gridboss] rows=1" in output
    assert "[slave]" not in output
    assert "excluded-session" not in output
    assert "excluded-error" not in output
    assert "session_change" in output
    assert '"previous_session": "session-before"' in output
    assert '"observation_gap_s": 10.0' in output
    assert '"continuity": "unknown"' in output
    assert "transaction=tx-without-time start=unavailable end=unavailable" in output
    assert "charge_w=" not in output
