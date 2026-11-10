"""Reconstruct saved recorder directories without a generator-low-power filter."""
from __future__ import annotations

import json
import subprocess
import sys

from tests.test_analyze_local_dongle_trace import ANALYZER


def test_directory_cli_reports_eps_anomaly_outside_generator_power_loss(tmp_path):
    """A 240-V EPS report above264 V matters while generator power stays normal."""
    root = tmp_path / "synthetic-recorder"
    raw = root / "raw"
    raw.mkdir(parents=True)
    (root / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": "synthetic-directory-proof",
    }))
    rows = [
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "gridboss", "ok": True,
            "sample_started_at": "2026-11-09T12:00:00+00:00",
            "sample_completed_at": "2026-11-09T12:00:01+00:00",
            "telemetry": {"decoded": {
                "generator_voltage_v": 240, "generator_frequency_hz": 60,
                "generator_power_w": 5000, "ups_voltage_v": 240,
                "ups_l1_voltage_v": 120, "ups_l2_voltage_v": 120,
            }},
        },
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "flexboss_master", "ok": True,
            "sample_started_at": "2026-11-09T12:00:02+00:00",
            "sample_completed_at": "2026-11-09T12:00:03+00:00",
            "telemetry": {"core": {"status_code": 32, "eps_voltage_r_v": 282.9}},
        },
        {
            "schema": "eg4_local_dongle_trace.v1", "role": "gridboss", "ok": True,
            "sample_started_at": "2026-11-09T12:00:04+00:00",
            "sample_completed_at": "2026-11-09T12:00:05+00:00",
            "telemetry": {"decoded": {
                "generator_voltage_v": 240, "generator_frequency_hz": 60,
                "generator_power_w": 5000, "ups_voltage_v": 240,
            }},
        },
    ]
    trace = raw / "local-dongle.jsonl"
    original = "".join(json.dumps(row) + "\n" for row in rows)
    trace.write_text(original)
    result = subprocess.run(
        [sys.executable, str(ANALYZER), str(root)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "[flexboss_master] rows=1" in result.stdout
    assert "[gridboss] rows=2" in result.stdout
    assert "output_voltage_anomaly" in result.stdout
    assert "282.9" in result.stdout
    assert trace.read_text() == original
