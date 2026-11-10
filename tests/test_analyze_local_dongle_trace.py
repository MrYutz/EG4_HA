"""Exercise the standalone analyzer through actual file and directory CLI inputs."""

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
    assert "[master] rows=4" in result.stdout
    assert "sha256=verified" in result.stdout
    assert "manifest_verification=complete" in result.stdout
    assert "transaction_failures=1 partial_transactions=1 semantic_rejections=1" in result.stdout
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
    assert "[master] rows=2" in result.stdout
    assert "[journal:master] rows=1" in result.stdout
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
