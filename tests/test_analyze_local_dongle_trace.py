"""Exercise the standalone analyzer through actual file and directory CLI inputs."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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
    master_sessions = {
        row["session_id"] for row in _structured(output, "role_coverage")
        if row["role"] == "master"
    }
    assert master_sessions == {"session-before", "session-after"}
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


def _invoke(path, *arguments):
    """Run the public, standalone CLI without importing the integration."""
    return subprocess.run(
        [sys.executable, str(ANALYZER), str(path), *arguments],
        capture_output=True, text=True, check=False, timeout=10,
    )


def _write_rows(path, rows):
    """Save clearly synthetic evidence and return its original bytes."""
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    return payload


def _bundle(tmp_path, rows):
    """Build a closed synthetic bundle using the public manifest contract."""
    import hashlib

    roles = sorted({row["role"] for row in rows})
    usable_roles = sorted({
        row["role"] for row in rows
        if row.get("ok") is True and row.get("telemetry")
        and row.get("kind") not in ("transaction", "interpretation")
    })
    missing_roles = sorted(set(roles) - set(usable_roles))
    bundle = tmp_path / "incident-synthetic"
    bundle.mkdir()
    payload = _write_rows(bundle / "records-0001.jsonl", rows)
    manifest = {
        "schema": "eg4_host_incident.v1", "incident_id": "synthetic",
        "session_id": "session-a", "build_id": "test-build", "entry_id": "test-entry",
        "evidence_class": "synthetic", "state": "closed",
        "started_at": "2026-11-09T12:00:00+00:00",
        "last_trigger_at": "2026-11-09T12:00:01+00:00",
        "closed_at": "2026-11-09T12:00:10+00:00",
        "expected_roles": roles, "observed_roles": usable_roles, "missing_roles": missing_roles,
        "complete": not missing_roles, "truncated": False, "incomplete_reasons": [], "errors": [],
        "gaps": [], "coverage": {
            "requested_pre_seconds": 0, "requested_post_seconds": 9,
            "actual_start": "2026-11-09T12:00:00+00:00",
            "actual_end": "2026-11-09T12:00:10+00:00",
            "pre_start": "2026-11-09T12:00:00+00:00",
            "pre_end": "2026-11-09T12:00:00+00:00",
            "warmup": False, "memory_truncated": False,
        },
        "configuration_context": {}, "configuration_observed_at": None,
        "triggers": [], "trigger_count": 0, "trigger_files": [],
        "files": [{
            "path": "records-0001.jsonl", "kind": "records", "bytes": len(payload),
            "finalized": True, "retained": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return bundle, manifest, payload


def test_incident_cli_verifies_hashes_and_preserves_partial_and_rejected_rows(tmp_path):
    bundle, _, original = _bundle(tmp_path, [
        {"kind": "transaction", "role": "master", "session_id": "session-a",
         "ok": False, "frames": [{"partial": True, "bytes_hex": "ab"}]},
        {"kind": "interpretation", "role": "master", "semantic": "rejected",
         "error": {"message": "invalid inverter status: 33280"}},
        {"schema": "eg4_local_dongle_trace.v1", "role": "master", "ok": False,
         "partial": True, "error": "legacy failed acquisition"},
        {"kind": "acquisition", "role": "master", "session_id": "session-a",
         "ok": True, "telemetry": {"core": {"status_code": 32}}},
    ])
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "sha256=verified" in result.stdout
    assert "manifest_verification=complete" in result.stdout
    assert "invalid inverter status: 33280" in result.stdout
    assert "acquisition_partials=1" in result.stdout
    assert "legacy failed acquisition" in result.stdout
    assert (bundle / "records-0001.jsonl").read_bytes() == original


def test_incident_cli_reports_hash_missing_role_gap_and_incomplete_evidence(tmp_path):
    bundle, manifest, _ = _bundle(tmp_path, [{
        "role": "master", "ok": True, "telemetry": {"core": {"status_code": 32}},
    }])
    manifest.update({
        "complete": False, "state": "interrupted", "truncated": True,
        "missing_roles": ["slave"], "expected_roles": ["master", "slave"],
        "incomplete_reasons": ["disk_pressure"],
        "gaps": [{"reason": "restart", "session_id": "session-b"}],
    })
    manifest["files"][0]["sha256"] = "0" * 64
    manifest["files"].append({
        "path": "records-missing.jsonl", "kind": "records", "bytes": 100,
        "finalized": True, "retained": True, "sha256": "1" * 64,
    })
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    before = (bundle / "manifest.json").read_bytes()
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    for evidence in (
        "sha256=mismatch", "missing_file", "slave", "restart",
        "disk_pressure", "interrupted", "manifest_verification=incomplete",
    ):
        assert evidence in result.stdout
    assert (bundle / "manifest.json").read_bytes() == before


def test_recorder_cli_does_not_count_incident_copies_as_rolling_acquisitions(tmp_path):
    root = tmp_path / "recorder"
    (root / "raw").mkdir(parents=True)
    (root / "incidents").mkdir()
    (root / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": "synthetic",
    }))
    rows = [
        {"role": "gridboss", "ok": True, "telemetry": {"decoded": {
            "generator_power_w": 5000, "generator_voltage_v": 240,
            "generator_frequency_hz": 60, "ups_voltage_v": 240,
            "ups_l1_voltage_v": 120, "ups_l2_voltage_v": 120,
        }}},
        {"role": "master", "ok": True, "telemetry": {"core": {
            "eps_voltage_r_v": 282.9, "eps_voltage_s_v": 120,
        }}},
    ]
    original = _write_rows(root / "raw" / "local-dongle.jsonl", rows)
    _bundle(root / "incidents", rows)
    result = _invoke(root)
    assert result.returncode == 0, result.stderr
    assert "[master] rows=1" in result.stdout
    assert "[gridboss] rows=1" in result.stdout
    assert "source_group=rolling incident_copies_excluded=true" in result.stdout
    assert "[incident:incident-synthetic:master] rows=1" in result.stdout
    assert "output_voltage_anomaly" in result.stdout
    assert "282.9" in result.stdout
    assert "sha256=verified" in result.stdout
    assert (root / "raw" / "local-dongle.jsonl").read_bytes() == original
    (root / "raw" / "local-dongle.jsonl").unlink()
    retained = _invoke(root)
    assert retained.returncode == 0, retained.stderr
    assert "[master] rows=" not in retained.stdout
    assert "[incident:incident-synthetic:master] rows=1" in retained.stdout
    assert "output_voltage_anomaly" in retained.stdout
    assert "282.9" in retained.stdout


def test_cli_brackets_outputs_without_treating_unknown_or_leg_channels_as_zero(tmp_path):
    rows = [
        {"role": "master", "ok": True, "session_id": "a",
         "sample_started_at": "2026-11-09T12:00:00+00:00",
         "sample_completed_at": "2026-11-09T12:00:03+00:00",
         "telemetry": {"core": {"eps_voltage_r_v": 240, "eps_voltage_s_v": 120}}},
        {"role": "master", "ok": True, "session_id": "a",
         "sample_started_at": "2026-11-09T12:00:02+00:00",
         "sample_completed_at": "2026-11-09T12:00:04+00:00",
         "telemetry": {"core": {"eps_voltage_r_v": 0}}},
        {"role": "master", "ok": False, "session_id": "a",
         "sample_started_at": "2026-11-09T12:00:05+00:00",
         "sample_completed_at": "2026-11-09T12:00:06+00:00"},
        {"role": "master", "ok": True, "session_id": "a",
         "sample_started_at": "2026-11-09T12:00:20+00:00",
         "sample_completed_at": "2026-11-09T12:00:21+00:00",
         "telemetry": {"core": {"eps_voltage_r_v": 240}}},
    ]
    output = _analyze(tmp_path, rows)
    assert output.count("  output_voltage_anomaly ") == 1
    assert "output_voltage_recovered" in output
    assert '"onset_bracket"' in output
    assert '"recovery_bracket"' in output
    assert '"ordering": "unresolved"' in output
    assert "acquisition_gap" in output
    assert "acquisition_failed" in output


def test_cli_reports_controller_protection_and_failed_configuration_age(tmp_path):
    output = _analyze(tmp_path, [{
        "schema": "eg4_controller_observation.v1", "kind": "acquisition",
        "role": "master", "observation": "controller_changed", "ok": False,
        "sample_started_at": "2026-11-09T12:10:00+00:00",
        "sample_completed_at": "2026-11-09T12:10:01+00:00",
        "changed_fields": ["input.6.bits_0_1", "input.519"],
        "controller": {"input": {"6": 3, "519": 256}},
        "availability": {"primary": False},
        "configuration_context": {
            "3:110:125": {
                "status": "failed", "effective_change_time_known": False,
                "latest_attempt": {
                    "transaction_id": "failed-now", "ok": False, "age_seconds": 1,
                    "completed_at": "2026-11-09T12:10:00+00:00",
                },
                "last_success": {
                    "transaction_id": "old-good", "ok": True, "age_seconds": 601,
                    "completed_at": "2026-11-09T12:00:00+00:00",
                },
            },
        },
    }])
    for evidence in (
        "controller_observation", "input.6.bits_0_1", '"519": 256',
        "configuration_context", "failed-now", "old-good",
        '"age_seconds": 601', '"status": "failed"',
        '"effective_change_time_known": false',
    ):
        assert evidence in output
    assert "status_run=" not in output


def test_incident_cli_does_not_follow_manifest_escape_or_symlinks(tmp_path):
    bundle, manifest, _ = _bundle(tmp_path, [{"role": "master", "ok": False}])
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"role":"escaped","error":"must-not-read"}\n')
    (bundle / "linked.jsonl").symlink_to(outside)
    for name in ("../outside.jsonl", "linked.jsonl", str(outside)):
        manifest["files"].append({
            "path": name, "kind": "records", "bytes": outside.stat().st_size,
            "finalized": False, "retained": True,
        })
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "unsafe_path" in result.stdout
    assert "[escaped]" not in result.stdout
    assert "must-not-read" not in result.stdout
    assert outside.read_text() == '{"role":"escaped","error":"must-not-read"}\n'


def test_cli_historical_directory_and_role_filter(tmp_path):
    _write_rows(tmp_path / "local-dongle-123.jsonl", [
        {"role": "master", "ok": False, "error": "historical-master"},
        {"role": "slave", "ok": False, "error": "excluded-slave"},
    ])
    (tmp_path / "unrelated.jsonl").write_text("not evidence\n")
    result = _invoke(tmp_path, "--role", "master")
    assert result.returncode == 0, result.stderr
    assert "[master] rows=1" in result.stdout
    assert "historical-master" in result.stdout
    assert "excluded-slave" not in result.stdout


def test_incident_cli_distinguishes_growing_unhashed_and_lost_files(tmp_path):
    bundle, manifest, _ = _bundle(tmp_path, [{"role": "master", "ok": False}])
    manifest.update({"state": "open", "complete": False, "closed_at": None})
    manifest["files"][0].update({"finalized": False})
    manifest["files"][0].pop("sha256")
    manifest["files"].append({
        "path": "pruned.jsonl", "kind": "records", "bytes": 99,
        "retained": False, "finalized": True,
    })
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "sha256=unavailable_growing" in result.stdout
    assert "lost_file=" in result.stdout
    assert "manifest_verification=incomplete" in result.stdout
    assert "[master] rows=1" in result.stdout
    manifest["files"][0]["finalized"] = True
    manifest.update({"state": "closed", "closed_at": "2026-11-09T12:00:10+00:00"})
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    finalized = _invoke(bundle)
    assert finalized.returncode == 0, finalized.stderr
    assert "sha256=missing_final_hash" in finalized.stdout


def test_cli_recovers_trigger_events_and_detects_missing_trigger_custody(tmp_path):
    import hashlib

    bundle, manifest, _ = _bundle(tmp_path, [{
        "role": "master", "ok": True, "telemetry": {"core": {"status_code": 32}},
    }])
    trigger = _write_rows(bundle / "triggers-0001.jsonl", [{
        "schema": "eg4_incident_trigger.v1", "kind": "incident_trigger",
        "incident_id": "synthetic", "session_id": "session-a", "role": "master",
        "observed_at": "2026-11-09T12:00:01+00:00",
        "reason": "output_voltage_anomaly",
        "details": {"channel": "eps_voltage_r_v", "voltage_v": 282.9},
    }])
    manifest["trigger_count"] = 1
    manifest["trigger_files"] = ["triggers-0001.jsonl"]
    manifest["files"].append({
        "path": "triggers-0001.jsonl", "kind": "triggers", "bytes": len(trigger),
        "finalized": True, "retained": True,
        "sha256": hashlib.sha256(trigger).hexdigest(),
    })
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "incident_trigger" in result.stdout
    assert "282.9" in result.stdout
    assert "manifest_verification=complete" in result.stdout
    (bundle / "triggers-0001.jsonl").unlink()
    missing = _invoke(bundle)
    assert missing.returncode == 0, missing.stderr
    assert "missing_file=" in missing.stdout
    assert "trigger_count_mismatch retained_trigger_count=0" in missing.stdout
    assert "manifest_verification=incomplete" in missing.stdout


def test_cli_reports_raw_input6_changes_without_installed_bit_interpretation(tmp_path):
    rows = []
    for word in (0, 3):
        rows.append({
            "kind": "acquisition", "role": "master", "ok": True,
            "session_id": "synthetic-controller",
            "telemetry": {"core": {"status_code": 32}},
            "primary_blocks": [{
                "function": 4, "register": 0, "ok": True,
                "values": [32, 0, 0, 0, 0, 0, word],
            }],
        })
    output = _analyze(tmp_path, rows)
    assert "controller_protection_change" in output
    assert '"input.6.bits_0_1": {"current": 3, "previous": 0}' in output
    assert '"authority": "raw_words"' in output


def test_cli_rejects_symlink_input_and_reports_malformed_line_location(tmp_path):
    trace = tmp_path / "local-dongle.jsonl"
    trace.write_text('{"role":"master","ok":false}\nnot-json\n')
    linked = tmp_path / "linked.jsonl"
    linked.symlink_to(trace)
    result = _invoke(linked)
    assert result.returncode == 2
    assert "unsafe_path" in result.stderr
    malformed = _invoke(trace)
    assert malformed.returncode == 2
    assert f"{trace}:2: invalid JSON" in malformed.stderr
    assert trace.read_text() == '{"role":"master","ok":false}\nnot-json\n'


def test_incident_cli_preserves_nested_configuration_roles_and_observed_age(tmp_path):
    bundle, manifest, _ = _bundle(tmp_path, [{"role": "master", "ok": False}])
    manifest["configuration_context"] = {
        "configuration_observed_at": "2026-11-09T12:00:01+00:00",
        "roles": {"master": {"3:110:125": {
            "status": "failed", "effective_change_time_known": False,
            "latest_attempt": {
                "transaction_id": "failed-context", "ok": False, "age_seconds": 2,
            },
            "last_success": {
                "transaction_id": "retained-context", "acquisition_id": "original-acq",
                "completed_at": "2026-11-09T11:50:00+00:00",
                "age_seconds": 601, "ok": True, "values": [1234] * 125,
            },
        }}},
        "role_freshness": {"inverters": {"SampleAgesSeconds": {"master": 7}}},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    for evidence in (
        '"roles": {"master"', '"configuration_observed_at": "2026-11-09T12:00:01+00:00"',
        "failed-context", "retained-context", "original-acq", '"age_seconds": 601',
        '"SampleAgesSeconds": {"master": 7}', '"effective_change_time_known": false',
    ):
        assert evidence in result.stdout
    assert "1234" not in result.stdout


def test_cli_journal_only_input_still_reports_reported_eps_anomalies(tmp_path):
    output = _analyze(tmp_path, [{
        "schema": "eg4_controller_observation.v1", "kind": "acquisition",
        "role": "master", "ok": True, "observation": "baseline",
        "telemetry": {"core": {"eps_voltage_r_v": 282.9}},
    }])
    assert "controller_observation" in output
    assert "output_voltage_anomaly" in output
    assert "282.9" in output
    assert "kinds={'controller_observation': 1}" in output


def test_cli_counts_structural_rejections_without_publishing_rejected_words(tmp_path):
    output = _analyze(tmp_path, [{
        "kind": "transaction", "role": "master", "ok": False,
        "transaction_id": "bad-crc", "frames": [{
            "bytes_hex": "abcd", "values": [33280],
            "validation": {"framing": "passed", "crc": "failed"},
        }],
    }])
    assert "raw_rejected" in output
    assert "raw_rejections=1" in output
    assert '"crc": "failed"' in output
    assert '"values": [33280]' in output
    assert "charge_w=" not in output
    assert "status_run=" not in output


def test_cli_retains_primary_failure_and_recovery_as_observations(tmp_path):
    rows = []
    for ok in (True, False, True):
        rows.append({
            "kind": "acquisition", "role": "master", "session_id": "synthetic",
            "ok": ok, "telemetry": {"core": {"status_code": 32}} if ok else None,
        })
    output = _analyze(tmp_path, rows)
    assert "acquisition_failures=1" in output
    assert '"availability.primary": {"current": false, "previous": true}' in output
    assert '"availability.primary": {"current": true, "previous": false}' in output


def test_cli_never_uses_persistence_time_as_missing_acquisition_coverage(tmp_path):
    output = _analyze(tmp_path, [{
        "schema": "eg4_local_dongle_trace.v1", "role": "master", "ok": True,
        "written_at": "2026-11-09T18:00:00+00:00",
        "telemetry": {"core": {"status_code": 32}},
    }])
    assert "coverage_start=unavailable coverage_end=unavailable" in output
    assert '"observed_through": "unavailable"' in output
    assert "18:00:00" not in output


def test_cli_reports_output_word_when_later_block_or_semantic_decode_fails(tmp_path):
    """A valid received output word must not vanish with the role's failed decode."""
    for role, output_register in (("gridboss", 2), ("flexboss_master", 20)):
        values = [0] * 40
        values[output_register] = 2829
        if role == "flexboss_master":
            values[0] = 33280
        trace = tmp_path / f"{role}-partial.jsonl"
        original = _write_rows(trace, [{
            "schema": "eg4_local_dongle_trace.v2",
            "kind": "acquisition", "role": role,
            "session_id": "synthetic-partial-output",
            "acquisition_id": "current-attempt", "ok": False, "partial": True,
            "sample_started_at": "2026-11-09T12:00:00+00:00",
            "sample_completed_at": "2026-11-09T12:00:06+00:00",
            "error": "later frequency timeout or unknown status",
            "primary_blocks": [{
                "function": 4, "register": 0, "count": 40, "ok": True,
                "acquisition_id": "current-attempt", "transaction_id": "power-block",
                "started_at": "2026-11-09T12:00:00+00:00",
                "completed_at": "2026-11-09T12:00:01+00:00",
                "semantic": "rejected" if role != "gridboss" else "not_evaluated",
                "values": values,
            }],
            "telemetry": None,
        }, {
            "schema": "eg4_local_dongle_trace.v2",
            "kind": "acquisition", "role": role,
            "session_id": "synthetic-partial-output",
            "acquisition_id": "next-failed-attempt", "ok": False,
            "sample_started_at": "2026-11-09T12:00:07+00:00",
            "sample_completed_at": "2026-11-09T12:00:08+00:00",
            "primary_blocks": [{
                "function": 4, "register": 0, "count": 40, "ok": True,
                "acquisition_id": "current-attempt", "current_acquisition": False,
                "values": [3000] * 40,
            }],
        }])
        result = _invoke(trace)
        assert result.returncode == 0, result.stderr
        events = [line for line in result.stdout.splitlines() if "output_voltage_anomaly " in line]
        assert len(events) == 1
        assert '"voltage_v": 282.9' in events[0]
        assert '"value_source": "structural_raw_register"' in events[0]
        assert "12:00:01" in events[0] and "12:00:06" not in events[0]
        assert "status_run=" not in result.stdout
        assert trace.read_bytes() == original


def test_incident_cli_orders_rotated_segments_before_recovered_active_file(tmp_path):
    """Manifest insertion order cannot reverse an observed anomaly and recovery."""
    import hashlib

    def sample(start, end, voltage):
        return {
            "kind": "acquisition", "role": "master", "session_id": "session-a",
            "ok": True, "sample_started_at": start, "sample_completed_at": end,
            "telemetry": {"core": {"eps_voltage_r_v": voltage}},
        }

    first = [
        sample("2026-11-09T12:00:00+00:00", "2026-11-09T12:00:01+00:00", 240),
        sample("2026-11-09T12:00:02+00:00", "2026-11-09T12:00:03+00:00", 282.9),
    ]
    bundle, manifest, _ = _bundle(tmp_path, first)
    (bundle / "records-0001.jsonl").unlink()
    (bundle / "records").mkdir()
    segments = [
        ("records/local-dongle-100.jsonl", first),
        ("records/local-dongle-200.jsonl", [
            sample("2026-11-09T12:00:04+00:00", "2026-11-09T12:00:05+00:00", 283.1),
        ]),
        ("records/local-dongle.jsonl", [
            sample("2026-11-09T12:00:06+00:00", "2026-11-09T12:00:07+00:00", 240),
        ]),
    ]
    entries = []
    originals = {}
    for name, rows in segments:
        payload = _write_rows(bundle / name, rows)
        originals[name] = payload
        entries.append({
            "path": name, "kind": "records", "bytes": len(payload),
            "retained": True, "finalized": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    manifest["files"] = list(reversed(entries))
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    events = [
        line for line in result.stdout.splitlines()
        if line.startswith(("  output_voltage_anomaly ", "  output_voltage_recovered "))
    ]
    assert len(events) == 3
    onset, ongoing, recovery = [json.loads(line.split(" details=", 1)[1]) for line in events]
    assert onset["voltage_v"] == 282.9
    assert onset["onset_bracket"]["previous_interval"] == [
        "2026-11-09T12:00:00+00:00", "2026-11-09T12:00:01+00:00",
    ]
    assert onset["onset_bracket"]["current_interval"] == [
        "2026-11-09T12:00:02+00:00", "2026-11-09T12:00:03+00:00",
    ]
    assert ongoing["voltage_v"] == 283.1
    assert ongoing["observation_bracket"]["previous_interval"] == [
        "2026-11-09T12:00:02+00:00", "2026-11-09T12:00:03+00:00",
    ]
    assert events[2].startswith("  output_voltage_recovered ")
    assert recovery["recovery_bracket"]["previous_interval"] == [
        "2026-11-09T12:00:04+00:00", "2026-11-09T12:00:05+00:00",
    ]
    assert recovery["recovery_bracket"]["current_interval"] == [
        "2026-11-09T12:00:06+00:00", "2026-11-09T12:00:07+00:00",
    ]
    assert recovery["recovery_bracket"]["transition_time"] == "unresolved"
    assert "manifest_verification=complete" in result.stdout
    for name, original in originals.items():
        assert (bundle / name).read_bytes() == original
    manifest["files"] = [None, {"path": 12}, *manifest["files"]]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    malformed = _invoke(bundle)
    assert malformed.returncode == 0, malformed.stderr
    assert "invalid_file_entry" in malformed.stdout
    assert "manifest_verification=incomplete" in malformed.stdout
    assert events == [
        line for line in malformed.stdout.splitlines()
        if line.startswith(("  output_voltage_anomaly ", "  output_voltage_recovered "))
    ]


def test_incident_cli_keeps_pre_window_session_boundary_without_false_incompleteness(tmp_path):
    """A known session start before retained coverage is context, not window loss."""
    bundle, manifest, _ = _bundle(tmp_path, [{
        "kind": "acquisition", "role": "master", "session_id": "session-a", "ok": True,
        "sample_started_at": "2026-11-09T12:00:00+00:00",
        "sample_completed_at": "2026-11-09T12:00:01+00:00",
        "telemetry": {"core": {"eps_voltage_r_v": 240}},
    }])
    manifest["gaps"] = [{
        "reason": "new_recorder_session", "session_id": "session-a",
        "observed_at": "2026-11-09T11:49:00+00:00", "prebuffer_warmup": True,
    }]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    original_manifest = (bundle / "manifest.json").read_bytes()
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "manifest_verification=complete" in result.stdout
    assert "new_recorder_session" in result.stdout
    assert "2026-11-09T11:49:00+00:00" in result.stdout
    assert '"affects_captured_coverage": false' in result.stdout
    assert (bundle / "manifest.json").read_bytes() == original_manifest
    manifest["files"][0]["sha256"] = "0" * 64
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    mismatched = _invoke(bundle)
    assert mismatched.returncode == 0, mismatched.stderr
    assert "sha256=mismatch" in mismatched.stdout
    assert "manifest_verification=incomplete" in mismatched.stdout


def test_incident_cli_never_ignores_unknown_within_window_or_unlocated_gaps(tmp_path):
    """Only a fully located, same-session, precoverage startup boundary is exempt."""
    baseline = {
        "reason": "new_recorder_session", "session_id": "session-a",
        "observed_at": "2026-11-09T11:49:00+00:00", "prebuffer_warmup": True,
    }
    cases = {
        "within_window": [{**baseline, "observed_at": "2026-11-09T12:00:05+00:00"}],
        "at_window_start": [{**baseline, "observed_at": "2026-11-09T12:00:00+00:00"}],
        "after_window": [{**baseline, "observed_at": "2026-11-09T12:00:20+00:00"}],
        "unlocated": [{key: value for key, value in baseline.items() if key != "observed_at"}],
        "malformed_time": [{**baseline, "observed_at": "not-a-time"}],
        "unknown_reason": [{**baseline, "reason": "raw_queue_lost"}],
        "unknown_session": [{**baseline, "session_id": "another-session"}],
        "malformed_boundary": [{**baseline, "prebuffer_warmup": "true"}],
        "malformed_entry": [None],
        "malformed_container": {},
        "unknown_coverage": [baseline],
    }
    for case, gaps in cases.items():
        root = tmp_path / case
        root.mkdir()
        bundle, manifest, _ = _bundle(root, [{
            "role": "master", "ok": True, "telemetry": {"core": {"status_code": 32}},
        }])
        manifest["gaps"] = gaps
        if case == "unknown_coverage":
            manifest["coverage"]["actual_start"] = None
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        result = _invoke(bundle)
        assert result.returncode == 0, (case, result.stderr)
        assert "manifest_verification=incomplete" in result.stdout, case
        assert '"affects_captured_coverage": false' not in result.stdout, case


def test_incident_cli_reports_later_all_role_context_without_overwriting_first_snapshot(tmp_path):
    """Later context records expose changed IDs/ages, not a settings-effective time."""
    import hashlib

    rows = [
        {"kind": "acquisition", "role": role, "session_id": "session-a", "ok": True,
         "telemetry": {"core": {"eps_voltage_r_v": 240}}}
        for role in ("master", "slave")
    ]
    bundle, manifest, _ = _bundle(tmp_path, rows)
    manifest["configuration_context"] = {
        "configuration_observed_at": "2026-11-09T12:00:00+00:00",
        "roles": {"master": {"3:110:125": {
            "status": "ok", "last_success": {
                "transaction_id": "first-master-good", "acquisition_id": "first-master-acq",
                "completed_at": "2026-11-09T11:59:50+00:00",
                "age_seconds": 10, "values": [987654321] * 125,
            },
        }}},
        "role_freshness": {"inverters": {"SampleAgesSeconds": {"master": 1, "slave": 2}}},
    }
    later = {
        "schema": "eg4_incident_context.v1", "kind": "incident_context",
        "incident_id": "synthetic", "session_id": "session-a",
        "observed_at": "2026-11-09T12:00:05+00:00",
        "configuration_observed_at": "2026-11-09T12:00:05+00:00",
        "roles": {
            "master": {"3:110:125": {
                "status": "failed", "effective_change_time_known": False,
                "latest_attempt": {
                    "transaction_id": "later-master-failed", "acquisition_id": "later-master-acq",
                    "completed_at": "2026-11-09T12:00:04+00:00",
                    "age_seconds": 1, "ok": False,
                },
                "last_success": {
                    "transaction_id": "first-master-good", "acquisition_id": "first-master-acq",
                    "completed_at": "2026-11-09T11:59:50+00:00",
                    "age_seconds": 15, "ok": True, "values": [987654321] * 125,
                },
            }},
            "slave": {"3:110:125": {
                "status": "ok", "last_success": {
                    "transaction_id": "later-slave-good", "acquisition_id": "later-slave-acq",
                    "completed_at": "2026-11-09T12:00:03+00:00",
                    "age_seconds": 2, "ok": True, "values": [987654321] * 125,
                },
            }},
        },
        "role_freshness": {"inverters": {"SampleAgesSeconds": {"master": 5, "slave": 2}}},
    }
    payload = _write_rows(bundle / "records-0001.jsonl", [*rows, later])
    manifest["files"][0].update({
        "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    })
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    original_manifest = (bundle / "manifest.json").read_bytes()
    result = _invoke(bundle, "--role", "master")
    assert result.returncode == 0, result.stderr
    first_event = next(
        line for line in result.stdout.splitlines() if line.startswith("  configuration_context ")
    )
    later_event = next(
        line for line in result.stdout.splitlines() if line.startswith("  incident_context ")
    )
    first_details = json.loads(first_event.split(" details=", 1)[1])
    later_details = json.loads(later_event.split(" details=", 1)[1])
    first_block = first_details["context"]["roles"]["master"]["3:110:125"]
    later_block = later_details["context"]["roles"]["master"]["3:110:125"]
    assert first_block["last_success"]["age_seconds"] == 10
    assert first_block["status"] == "ok"
    assert later_details["observed_at"] == "2026-11-09T12:00:05+00:00"
    assert later_details["configuration_observed_at"] == "2026-11-09T12:00:05+00:00"
    assert later_details["effective_change_time_known"] is False
    assert later_block["status"] == "failed"
    assert later_block["latest_attempt"]["transaction_id"] == "later-master-failed"
    assert later_block["latest_attempt"]["age_seconds"] == 1
    assert later_block["last_success"]["transaction_id"] == "first-master-good"
    assert later_block["last_success"]["completed_at"] == "2026-11-09T11:59:50+00:00"
    assert later_block["last_success"]["age_seconds"] == 15
    assert later_details["context"]["roles"]["slave"]["3:110:125"]["last_success"][
        "transaction_id"
    ] == "later-slave-good"
    assert later_details["context"]["role_freshness"]["inverters"]["SampleAgesSeconds"] == {
        "master": 5, "slave": 2,
    }
    assert "987654321" not in result.stdout
    assert "[master] rows=1" in result.stdout
    assert "[slave]" not in result.stdout
    assert "[unknown]" not in result.stdout
    assert (bundle / "records-0001.jsonl").read_bytes() == payload
    assert (bundle / "manifest.json").read_bytes() == original_manifest


def test_recorder_cli_reports_crash_tail_and_continues_later_sessions_read_only(tmp_path):
    """An old partial JSON tail cannot hide a later session or a safe journal."""
    root = tmp_path / "recorder"
    raw = root / "raw"
    raw.mkdir(parents=True)
    journal = root / "controller-observations"
    journal.mkdir()
    (root / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": "synthetic-crash-tail",
    }))
    old_path = raw / "local-dongle-100.jsonl"
    old_prefix = _write_rows(old_path, [{
        "kind": "acquisition", "role": "master", "session_id": "old-session",
        "acquisition_id": "old-primary", "ok": True,
        "sample_started_at": "2026-11-09T12:00:00+00:00",
        "sample_completed_at": "2026-11-09T12:00:01+00:00",
        "telemetry": {"core": {"eps_voltage_r_v": 240}},
    }])
    damaged = old_prefix + b'{"kind":"acquisition","session_id":"old-session"'
    old_path.write_bytes(damaged)
    active = raw / "local-dongle.jsonl"
    later = _write_rows(active, [{
        "kind": "acquisition", "role": "master", "session_id": "later-session",
        "acquisition_id": "later-primary", "ok": True,
        "sample_started_at": "2026-11-09T12:00:20+00:00",
        "sample_completed_at": "2026-11-09T12:00:21+00:00",
        "telemetry": {"core": {"eps_voltage_r_v": 282.9}},
    }])
    journal_path = journal / "local-dongle.jsonl"
    journal_bytes = _write_rows(journal_path, [{
        "schema": "eg4_controller_observation.v1", "kind": "acquisition",
        "role": "master", "session_id": "later-session",
        "observation": "recovery_baseline", "ok": False,
        "controller": {"input": {"6": 3}},
    }])
    result = _invoke(root)
    assert result.returncode == 2, result.stderr
    assert f"{old_path}:2: invalid JSON" in result.stdout
    assert "input_file_gap=" in result.stdout
    assert '"continuity": "unknown"' in result.stdout
    assert "old-primary" in result.stdout
    assert "later-primary" in result.stdout
    assert "session=later-session" in result.stdout
    assert "session_change" in result.stdout
    assert "output_voltage_anomaly" in result.stdout
    assert "282.9" in result.stdout
    assert "directory analysis incomplete" in result.stderr
    strict = _invoke(old_path)
    assert strict.returncode == 2
    assert f"{old_path}:2: invalid JSON" in strict.stderr
    assert "later-session" not in strict.stdout
    assert old_path.read_bytes() == damaged
    assert active.read_bytes() == later
    assert journal_path.read_bytes() == journal_bytes


def test_cli_recovers_raw_only_output_and_deduplicates_its_later_summary(tmp_path):
    """A flushed input block survives a crash before its acquisition summary."""
    for role, output_register in (("gridboss", 2), ("flexboss_master", 20)):
        for include_summary in (False, True):
            values = [0] * 40
            values[output_register] = 2829
            transaction = {
                "schema": "eg4_local_dongle_trace.v2", "kind": "transaction",
                "role": role, "session_id": "raw-only-crash",
                "acquisition_id": "output-source", "transaction_id": "raw-power",
                "ok": True, "started_at": "2026-11-09T12:00:00+00:00",
                "completed_at": "2026-11-09T12:00:01+00:00",
                "request": {"function": 4, "register": 0, "count": 40},
                "frames": [{"classification": "matching", "values": values}],
            }
            records = [transaction]
            if include_summary:
                records.append({
                    **transaction, "kind": "acquisition",
                    "sample_completed_at": "2026-11-09T12:00:06+00:00",
                    "telemetry": {"decoded": {"ups_voltage_v": 282.9},
                                  "core": {"eps_voltage_r_v": 282.9}},
                })
            trace = tmp_path / f"{role}-{include_summary}.jsonl"
            original = _write_rows(trace, records)
            result = _invoke(trace)
            assert result.returncode == 0, result.stderr
            events = [line for line in result.stdout.splitlines()
                      if "output_voltage_anomaly " in line]
            assert len(events) == 1
            assert '"voltage_v": 282.9' in events[0]
            assert '"value_source": "structural_raw_register"' in events[0]
            assert "raw-power" in events[0] and "12:00:01" in events[0]
            assert trace.read_bytes() == original


def _controller_transaction(role, function, register, count, offset, word, sequence):
    """Represent one successful stock-profile read, not decoded telemetry."""
    values = [0] * count
    values[offset] = word
    started = datetime.fromisoformat("2026-11-09T12:00:00+00:00") + timedelta(seconds=sequence * 2)
    return {
        "schema": "eg4_local_dongle_trace.v2", "kind": "transaction",
        "role": role, "session_id": "synthetic-raw-controller",
        "acquisition_id": f"acq-{sequence}", "transaction_id": f"tx-{sequence}",
        "started_at": started.isoformat(),
        "completed_at": (started + timedelta(seconds=1)).isoformat(),
        "ok": True, "request": {
            "function": function, "register": register, "count": count,
        },
        "frames": [{
            "classification": "matching", "values": values,
            "validation": {name: "passed" for name in (
                "framing", "crc", "identity", "function", "range",
            )},
        }],
    }


def _controller_summary(transaction, *, cached=False):
    block = {
        **transaction["request"], "ok": True,
        **{name: transaction[name] for name in (
            "acquisition_id", "transaction_id", "started_at", "completed_at",
        )},
        "values": transaction["frames"][0]["values"],
        "current_acquisition": not cached,
    }
    return {
        "schema": "eg4_local_dongle_trace.v2", "kind": "acquisition",
        "role": transaction["role"], "session_id": transaction["session_id"],
        "acquisition_id": "later-acquisition" if cached else transaction["acquisition_id"],
        "sample_started_at": "2026-11-09T12:00:10+00:00",
        "sample_completed_at": "2026-11-09T12:00:11+00:00",
        "ok": True, "diagnostics": [block],
    }


@pytest.mark.parametrize(
    "role,function,register,count,offset,before,after,expected",
    [
        ("gridboss", 4, 500, 100, 19, 0x0101, 0x0201, {"input.519": (257, 513)}),
        ("gridboss", 4, 500, 100, 19, 0x0101, 0x0102, {"input.519": (257, 258)}),
        ("flexboss_master", 4, 0, 40, 6, 0, 3,
         {"input.6": (0, 3), "input.6.bits_0_1": (0, 3)}),
        ("flexboss_master", 4, 40, 40, 20, 0, 8, {"input.60": (0, 8)}),
        ("flexboss_slave", 4, 120, 26, 24, 0, 4, {"input.144": (0, 4)}),
        ("flexboss_master", 3, 110, 125, 60, 0, 2, {"holding.170": (0, 2)}),
        ("gridboss", 3, 4500, 10, 0, 0, 1, {"holding.4500": (0, 1)}),
        ("gridboss", 3, 2000, 108, 102, 0, 16, {"holding.2102": (0, 16)}),
    ],
)
@pytest.mark.parametrize("summaries", ["absent", "immediate", "delayed_cached"])
def test_cli_recovers_raw_controller_changes_without_summary_replay(
    tmp_path, role, function, register, count, offset, before, after, expected, summaries,
):
    first = _controller_transaction(role, function, register, count, offset, before, 0)
    changed = _controller_transaction(role, function, register, count, offset, after, 1)
    rows = [first, changed]
    if summaries == "immediate":
        rows = [first, _controller_summary(first), changed, _controller_summary(changed)]
    elif summaries == "delayed_cached":
        rows.extend([_controller_summary(first, cached=True), _controller_summary(changed)])
    output = _analyze(tmp_path, rows)
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 1
    event = events[0]
    details = json.loads(event.split(" details=", 1)[1])
    assert details["changed"] == {
        key: {"previous": old, "current": new} for key, (old, new) in expected.items()
    }
    assert "acquisition=acq-1 transaction=tx-1" in event
    assert f"start={changed['started_at']} end={changed['completed_at']}" in event
    assert details["authority"] == "raw_words"
    assert "status_run=" not in output


@pytest.mark.parametrize("rejection", [
    "identity", "unrelated", "crc", "range", "partial", "short", "negative", "overflow", "boolean",
])
def test_cli_does_not_promote_rejected_or_invalid_controller_frames(tmp_path, rejection):
    baseline = _controller_transaction("flexboss_master", 4, 0, 40, 6, 0, 0)
    rejected = _controller_transaction("flexboss_master", 4, 0, 40, 6, 3, 1)
    frame = rejected["frames"][0]
    if rejection in ("identity", "unrelated"):
        frame["classification"] = rejection
    elif rejection in ("crc", "range"):
        frame["validation"][rejection] = "failed"
    elif rejection == "partial":
        frame["partial"] = True
    elif rejection == "short":
        frame["values"] = frame["values"][:7]
    else:
        frame["values"][6] = {"negative": -1, "overflow": 65536, "boolean": True}[rejection]
    output = _analyze(tmp_path, [baseline, rejected])
    assert "controller_protection_change " not in output


def test_cli_resets_controller_comparison_and_source_dedup_at_session_change(tmp_path):
    rows = []
    for session, words in (("before-restart", (0, 3)), ("after-restart", (8, 1))):
        for sequence, word in enumerate(words):
            transaction = _controller_transaction("flexboss_master", 4, 0, 40, 6, word, sequence)
            transaction["session_id"] = session
            rows.extend([transaction, _controller_summary(transaction)])
    output = _analyze(tmp_path, rows)
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 2
    assert '"input.6": {"current": 3, "previous": 0}' in events[0]
    assert '"input.6": {"current": 1, "previous": 8}' in events[1]
    assert "session_change " in output
    assert '"previous_session": "before-restart"' in output


@pytest.mark.parametrize("cached", [False, True])
def test_cli_does_not_regress_to_older_uncaptured_summary_sources(tmp_path, cached):
    baseline = _controller_transaction("gridboss", 4, 500, 100, 19, 0, 0)
    changed = _controller_transaction("gridboss", 4, 500, 100, 19, 256, 1)
    uncaptured = {
        **baseline, "transaction_id": "uncaptured-old-transaction",
        "acquisition_id": "uncaptured-old-acquisition",
    }
    resumed = _controller_transaction("gridboss", 4, 500, 100, 19, 257, 2)
    output = _analyze(tmp_path, [
        baseline, changed, _controller_summary(uncaptured, cached=cached), resumed,
    ])
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 2
    assert '"input.519": {"current": 256, "previous": 0}' in events[0]
    assert '"input.519": {"current": 257, "previous": 256}' in events[1]
    assert "transaction=tx-2" in events[1]


def test_cli_does_not_replay_old_controller_facts_after_long_capture(tmp_path):
    """An hour of intervening transactions cannot make an old summary current."""
    rows = [
        _controller_transaction("gridboss", 4, 500, 100, 19, 0, sequence)
        for sequence in range(2100)
    ]
    rows.append(_controller_transaction("gridboss", 4, 500, 100, 19, 256, 2100))
    rows.append(_controller_summary(rows[0], cached=True))
    output = _analyze(tmp_path, rows)
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 1
    assert '"input.519": {"current": 256, "previous": 0}' in events[0]
    assert "transaction=tx-2100" in events[0]


def test_cli_keeps_historical_raw_controller_changes(tmp_path):
    rows = []
    for sequence, word in enumerate((257, 513)):
        transaction = _controller_transaction("gridboss", 4, 500, 100, 19, word, sequence)
        rows.append({
            "schema": "eg4_local_dongle_trace.v1", "role": "gridboss", "ok": True,
            "sample_started_at": transaction["started_at"],
            "sample_completed_at": transaction["completed_at"],
            "telemetry": {"raw": {
                "input_registers_start_500": transaction["frames"][0]["values"],
            }},
        })
    output = _analyze(tmp_path, rows)
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 1
    assert '"input.519": {"current": 513, "previous": 257}' in events[0]
    assert "session=unavailable acquisition=unavailable transaction=unavailable" in events[0]


def test_cli_keeps_new_raw_controller_changes_across_host_clock_regression(tmp_path):
    """A new transaction remains evidence when UTC moves backward within a session."""
    first = _controller_transaction("gridboss", 4, 500, 100, 19, 257, 0)
    changed = _controller_transaction("gridboss", 4, 500, 100, 19, 513, 1)
    changed["started_at"] = "2026-11-09T11:59:58+00:00"
    changed["completed_at"] = "2026-11-09T11:59:59+00:00"
    output = _analyze(tmp_path, [first, changed, _controller_summary(changed)])
    events = [line for line in output.splitlines() if "controller_protection_change " in line]
    assert len(events) == 1
    details = json.loads(events[0].split(" details=", 1)[1])
    assert details["changed"]["input.519"] == {"previous": 257, "current": 513}
    assert details["observation_brackets"]["input.519"]["ordering"] == "unresolved"
    assert "transaction=tx-1" in events[0]
    assert "start=2026-11-09T11:59:58+00:00" in events[0]


def _structured(output, name):
    """Read public JSON evidence fragments, not presentation prose."""
    prefix = f"{name}="
    return [
        json.loads(line.strip()[len(prefix):])
        for line in output.splitlines() if line.strip().startswith(prefix)
    ]


def _timed_read(sequence, offset, duration, *, build="synthetic-build", session="coverage-session"):
    row = _controller_transaction("gridboss", 4, 0, 40, 3, 2400, sequence)
    start = datetime.fromisoformat("2026-11-09T12:00:00+00:00") + timedelta(seconds=offset)
    row.update({
        "build_id": build, "session_id": session,
        "started_at": start.isoformat(),
        "completed_at": (start + timedelta(seconds=duration)).isoformat(),
        "duration_ms": duration * 1000,
    })
    return row


def test_cli_deduplicates_source_copies_before_quantiles_and_late_rejection(tmp_path):
    reads = [_timed_read(index, offset, duration) for index, (offset, duration) in enumerate(
        ((0, .1), (2, .2), (12, .3), (42, .4))
    )]
    cached = {**_controller_summary(reads[1], cached=True), "build_id": "synthetic-build"}
    reads[3].update(ok=False, frames=[], error={"message": "synthetic timeout"})
    output = _analyze(tmp_path, [
        reads[0], reads[1], cached, reads[2],
        {**reads[2], "kind": "interpretation", "semantic": "rejected"},
        reads[3], reads[1],
    ], "--registers")
    blocks = _structured(output, "block_coverage")
    assert len(blocks) == 1
    block = blocks[0]
    assert (block["samples"], block["successes"], block["failures"], block["semantic_rejections"]) == (4, 2, 1, 1)
    assert block["deduplicated_copies"] == 2
    assert block["spacing_seconds"] == {"count": 3, "median": 10, "p95": 30, "max": 30}
    assert block["latency_ms"] == {"count": 4, "median": 250, "p95": 400, "max": 400}
    assert block["latest_attempt"]["transaction_id"] == "tx-3"
    assert block["last_good"]["transaction_id"] == "tx-1"
    assert block["last_good"]["age_at_evidence_end_seconds"] == pytest.approx(40.2)
    rejected = next(row for row in _structured(output, "register_projection")
                    if row["source"]["transaction_id"] == "tx-2")
    assert rejected["quality"]["semantic"] == "rejected"
    assert rejected["quality"]["telemetry_usable"] is False
    assert rejected["registers"]


def test_cli_never_pools_reused_transaction_ids_across_builds_or_sessions(tmp_path):
    rows = [
        _timed_read(0, 0, .1, build="old", session="one"),
        _timed_read(0, 10, .2, build="new", session="one"),
        _timed_read(0, 20, .3, build="new", session="two"),
        _timed_read(0, 30, .4, build=None, session="one"),
    ]
    output = _analyze(tmp_path, rows)
    blocks = _structured(output, "block_coverage")
    assert {(row["build_id"], row["session_id"]) for row in blocks} == {
        ("old", "one"), ("new", "one"), ("new", "two"), (None, "one"),
    }
    assert all(row["samples"] == 1 and row["spacing_seconds"]["count"] == 0 for row in blocks)
    identities = _structured(output, "summary_identity")
    assert {(row["build_id"], row["session_id"]) for row in identities} == {
        ("old", "one"), ("new", "one"), ("new", "two"), (None, "one"),
    }


def test_cli_reports_captured_missing_roles_and_blocks_despite_healthy_metadata(tmp_path):
    profile = {
        "schema": "synthetic-captured-profile", "revision": "captured-not-current",
        "primary": [[4, 0, 40]], "dynamic": [],
        "maintenance": [[3, 4097, 41, 300]], "effective_periods": [[3, 4097, 41, 300]],
    }
    health = {
        "kind": "recorder_health", "role": "recorder", "build_id": "synthetic-build",
        "session_id": "coverage-session", "observed_at": "2026-11-09T12:00:01+00:00",
        "health": {
            "State": "healthy", "ConfiguredRoles": ["master", "slave", "gridboss"],
            "Roles": {role: {"State": "healthy", "capture_profile": profile}
                      for role in ("master", "slave", "gridboss")},
            "RawPersistence": {"State": "healthy", "DroppedRecords": 4},
        },
    }
    output = _analyze(tmp_path, [_timed_read(0, 0, .1), health])
    roles = {row["role"]: row for row in _structured(output, "role_coverage")}
    assert roles["master"]["status"] == roles["slave"]["status"] == "missing"
    assert roles["gridboss"]["missing_expected_blocks"] == 1
    assert all(row["sample_sufficiency"] == "insufficient" for row in roles.values())
    assert all(row["retention_or_gap_limit"] for row in roles.values())
    missing = [row for row in _structured(output, "block_coverage")
               if row["role"] == "gridboss" and row["register"] == 4097]
    assert len(missing) == 1
    assert missing[0]["samples"] == 0
    assert missing[0]["last_good"] is None
    assert missing[0]["expectation"]["period_seconds"] == 300
    assert all(row["profile"]["revision"] == "captured-not-current" for row in roles.values())


def test_cli_source_query_skew_uses_original_queries_not_cached_summary_time(tmp_path):
    voltage = _timed_read(0, 0, .1)
    frequency = _timed_read(1, 3, .1)
    frequency["acquisition_id"] = voltage["acquisition_id"]
    frequency["request"] = {"function": 4, "register": 120, "count": 12}
    frequency["frames"][0]["values"] = [0] * 10 + [6000, 0]
    summary = {
        **_controller_summary(voltage, cached=True), "build_id": "synthetic-build",
        "sample_started_at": "2026-11-09T12:00:50+00:00",
        "sample_completed_at": "2026-11-09T12:01:00+00:00",
    }
    output = _analyze(tmp_path, [voltage, frequency, summary])
    skew = _structured(output, "source_query_skew")
    assert len(skew) == 1
    assert skew[0]["start_skew_seconds"] == {"count": 1, "median": 3, "p95": 3, "max": 3}
    first = next(row for row in _structured(output, "block_coverage") if row["register"] == 0)
    assert first["samples"] == 1
    assert first["last_good"]["age_at_evidence_end_seconds"] == pytest.approx(59.9)
    assert first["spacing_seconds"]["count"] == 0


def test_cli_deduplicates_rolling_acquisition_and_journal_sources(tmp_path):
    raw = tmp_path / "raw"
    journal = tmp_path / "controller-observations"
    raw.mkdir()
    journal.mkdir()
    transaction = _timed_read(0, 0, .1)
    summary = {**_controller_summary(transaction), "build_id": "synthetic-build"}
    raw_bytes = _write_rows(raw / "local-dongle.jsonl", [transaction, summary])
    journal_bytes = _write_rows(journal / "local-dongle.jsonl", [
        {**summary, "schema": "eg4_controller_observation.v1"},
    ])
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    blocks = _structured(result.stdout, "block_coverage")
    assert len(blocks) == 1
    assert blocks[0]["samples"] == 1
    assert blocks[0]["deduplicated_copies"] == 2
    assert (raw / "local-dongle.jsonl").read_bytes() == raw_bytes
    assert (journal / "local-dongle.jsonl").read_bytes() == journal_bytes


@pytest.mark.parametrize("wrapper", ["gridboss", "flexboss_master", "reads"])
def test_cli_reads_retained_baseline_wrappers_without_inventing_build_or_query_ids(tmp_path, wrapper):
    block = {
        "function": 4, "start": 10, "value_or_count": 1, "ok": True, "values": [123],
        "started_at": "2026-11-09T12:00:00+00:00",
        "completed_at": "2026-11-09T12:00:00.250000+00:00", "duration_ms": 250,
        "received_frames": [{"accepted": False}, {"accepted": True}],
    }
    document = {"schema": "eg4_extended_baseline.v1", "role": "gridboss",
                "captured_at": "2026-11-09T12:00:01+00:00"}
    if wrapper == "gridboss":
        document["blocks"] = [block]
    elif wrapper == "flexboss_master":
        document.update(role=wrapper, initial_q1_readonly={
            "transactions": [block, {"function": 7, "start": 0, "value_or_count": 5}]},
            additional_blocks=[block])
    else:
        document["reads"] = [block]
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    result = _invoke(path, "--registers")
    assert result.returncode == 0, result.stderr
    blocks = _structured(result.stdout, "block_coverage")
    assert len(blocks) == 1 and blocks[0]["samples"] == 1
    assert blocks[0]["build_id"] is None and blocks[0]["session_id"] is None
    assert blocks[0]["latency_ms"]["median"] == 250
    projection = _structured(result.stdout, "register_projection")[0]
    assert projection["source"]["transaction_id"] is None
    assert projection["source"]["baseline_frame_acceptance"] == [False, True]
    assert projection["registers"][0]["raw"] == [123]
    assert projection["registers"][0]["unit"] is None
    assert not any(row["category"] == "invalid_source" for row in _structured(result.stdout, "coverage_fact"))
    assert path.read_bytes() == before


def test_cli_projection_preserves_incomplete_invalid_and_uncalibrated_words(tmp_path):
    current = _controller_transaction("gridboss", 4, 10, 1, 0, 123, 0)
    incomplete = _controller_transaction("gridboss", 4, 84, 1, 0, 65535, 1)
    invalid = _controller_transaction("gridboss", 4, 26, 1, 0, True, 2)
    output = _analyze(tmp_path, [current, incomplete, invalid], "--registers")
    projections = {row["shape"][1]: row for row in _structured(output, "register_projection")}
    assert projections[10]["registers"][0]["value"] == 123
    assert projections[10]["registers"][0]["unit"] is None
    assert projections[84]["registers"][0]["value"] is None
    assert projections[84]["registers"][0]["addresses"] == [84]
    assert projections[26]["registers"][0]["value"] is None
    assert projections[26]["registers"][0]["raw"] == [True]
    assert projections[26]["quality"]["structural"] == "rejected"
    assert projections[26]["quality"]["telemetry_usable"] is False


def test_cli_oversized_projection_is_explicitly_unusable_without_amplifying_words(tmp_path):
    transaction = _controller_transaction("gridboss", 4, 0, 40, 3, 2400, 0)
    transaction["frames"][0]["values"] = [1] * 200_000
    output = _analyze(tmp_path, [transaction], "--registers")
    assert not _structured(output, "register_projection")
    facts = _structured(output, "coverage_fact")
    invalid = next(row for row in facts if row["category"] == "invalid_source")
    assert invalid["reported"]["word_count"] == 200_000
    assert invalid["limits_sample_sufficiency"] is True
    role = _structured(output, "role_coverage")[0]
    assert role["sample_sufficiency"] == "insufficient"
    assert role["observed_blocks"] is False


def test_cli_inventory_last_success_keeps_original_age_after_failed_attempt(tmp_path):
    good = _timed_read(0, 0, .1)
    failed = _timed_read(1, 20, .1)
    good_block = {**good["request"], **{key: good[key] for key in (
        "transaction_id", "acquisition_id", "started_at", "completed_at",
    )}, "ok": True, "values": good["frames"][0]["values"], "semantic": "accepted"}
    failed_block = {**good_block, "transaction_id": failed["transaction_id"],
                    "acquisition_id": failed["acquisition_id"],
                    "started_at": failed["started_at"], "completed_at": failed["completed_at"],
                    "ok": False, "values": None, "semantic": "not_evaluated"}
    record = {
        "kind": "acquisition", "role": "gridboss", "build_id": "synthetic-build",
        "session_id": "coverage-session", "acquisition_id": "later",
        "sample_completed_at": "2026-11-09T12:00:30+00:00", "ok": False,
        "inventory_context": {"4:0:40": {
            "function": 4, "register": 0, "count": 40,
            "latest_attempt": failed_block, "last_success": good_block,
        }},
    }
    output = _analyze(tmp_path, [record])
    block = _structured(output, "block_coverage")[0]
    assert (block["samples"], block["successes"], block["failures"]) == (2, 1, 1)
    assert block["latest_attempt"]["transaction_id"] == "tx-1"
    assert block["last_good"]["transaction_id"] == "tx-0"
    assert block["last_good"]["age_at_evidence_end_seconds"] == pytest.approx(29.9)
    assert block["latency_ms"]["count"] == 2


def test_cli_cached_copy_cannot_rescue_failed_canonical_transaction(tmp_path):
    transaction = _timed_read(0, 0, .1)
    copy = {**_controller_summary(transaction), "build_id": "synthetic-build"}
    transaction.update(ok=False, frames=[])
    output = _analyze(tmp_path, [transaction, copy])
    block = _structured(output, "block_coverage")[0]
    assert block["samples"] == 1
    assert block["conflicting_sources"] == 1
    assert block["failures"] == 1 and block["successes"] == 0
    assert block["last_good"] is None
    assert block["latest_attempt"]["structural"] == "rejected"


def test_cli_does_not_pair_ambiguous_repeated_queries_as_simultaneous_samples(tmp_path):
    first = _timed_read(0, 0, .1)
    retry = _timed_read(1, 2, .1)
    frequency = _timed_read(2, 4, .1)
    retry["acquisition_id"] = frequency["acquisition_id"] = first["acquisition_id"]
    frequency["request"] = {"function": 4, "register": 120, "count": 12}
    frequency["frames"][0]["values"] = [0] * 12
    output = _analyze(tmp_path, [first, retry, frequency])
    skew = _structured(output, "source_query_skew")[0]
    assert skew["start_skew_seconds"]["count"] == 0
    assert skew["unpaired_or_ambiguous_acquisitions"] == 1


def test_cli_complete_manifest_does_not_attest_register_sample_coverage(tmp_path):
    bundle, _, _ = _bundle(tmp_path, [{
        "kind": "acquisition", "role": "gridboss", "session_id": "session-a",
        "build_id": "test-build", "ok": True,
        "started_at": "2026-11-09T12:00:00+00:00",
        "completed_at": "2026-11-09T12:00:01+00:00",
        "telemetry": {"decoded": {"ups_voltage_v": 240}},
    }])
    result = _invoke(bundle)
    assert result.returncode == 0, result.stderr
    assert "manifest_verification=complete" in result.stdout
    role = _structured(result.stdout, "role_coverage")[0]
    assert role["role_observed"] is True
    assert role["observed_blocks"] is False
    assert role["sample_sufficiency"] == "insufficient"
    assert role["manifest_custody_is_sample_coverage"] is False


def test_cli_raw_voltage_frequency_query_skew_survives_semantic_rejection(tmp_path):
    voltage = _timed_read(0, 0, .1)
    frequency = _timed_read(1, 3, .1)
    frequency["acquisition_id"] = voltage["acquisition_id"]
    frequency["request"] = {"function": 4, "register": 120, "count": 12}
    frequency["frames"][0]["values"] = [0] * 12
    output = _analyze(tmp_path, [
        voltage, frequency, {**frequency, "kind": "interpretation", "semantic": "rejected"},
    ])
    skew = _structured(output, "source_query_skew")[0]
    assert skew["start_skew_seconds"]["median"] == 3
    assert skew["start_skew_seconds"]["count"] == 1
    assert skew["semantic_rejected_sources"] == 1
    rejected = next(row for row in _structured(output, "block_coverage") if row["register"] == 120)
    assert rejected["semantic_rejections"] == 1 and rejected["successes"] == 0


def test_cli_never_applies_gridboss_voltage_frequency_addresses_to_flex(tmp_path):
    rows = [
        _controller_transaction("flexboss_master", 4, 0, 40, 3, 2400, 0),
        _controller_transaction("flexboss_master", 4, 120, 26, 10, 6000, 1),
    ]
    rows[1]["acquisition_id"] = rows[0]["acquisition_id"]
    output = _analyze(tmp_path, rows)
    assert not _structured(output, "source_query_skew")
    assert len(_structured(output, "block_coverage")) == 2


@pytest.mark.parametrize("regression", [False, True])
def test_cli_flags_canonical_clock_order_not_replayed_cache_age(tmp_path, regression):
    first = _timed_read(0, 0, .1)
    second = _timed_read(1, 10, .1)
    cached = {**_controller_summary(first, cached=True), "build_id": "synthetic-build"}
    third = _timed_read(2, 5 if regression else 20, .1)
    output = _analyze(tmp_path, [first, second, cached, third])
    role = _structured(output, "role_coverage")[0]
    assert role["canonical_time_regressions"] == int(regression)
    block = _structured(output, "block_coverage")[0]
    assert block["samples"] == 3
    assert block["latest_attempt"]["transaction_id"] == "tx-2"
    if regression:
        assert block["spacing_seconds"]["count"] == 0
        assert block["interior_gaps"] is None
        assert block["last_good"]["age_at_evidence_end_seconds"] is None
        assert role["sample_sufficiency"] == "insufficient"
        assert any(row["category"] == "canonical_source_time_regression"
                   for row in _structured(output, "coverage_fact"))
    else:
        assert block["spacing_seconds"]["count"] == 2
