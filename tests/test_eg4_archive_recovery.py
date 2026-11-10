"""Process-death and analyzer regressions at real archive filesystem seams."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.eg4_archive import ArchiveError, LocalSource, archive_once

ENTRY = "synthetic-recovery"
WHEN = datetime(2026, 11, 10, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for name in ("raw", "controller-observations", "incidents"):
        (source / name).mkdir(parents=True)
    (source / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": ENTRY,
    }))
    return source


def _run(source: Path, destination: Path, days: int = 0) -> dict:
    return archive_once(LocalSource(source), destination, entry_id=ENTRY,
                        now=WHEN + timedelta(days=days), min_free_bytes=0)


def _incident(source: Path, data: bytes) -> Path:
    incident = source / "incidents" / "incident-synthetic"
    (incident / "records").mkdir(parents=True)
    (incident / "records" / "local-dongle.jsonl").write_bytes(data)
    _manifest(incident)
    return incident


def _manifest(incident: Path, *, closed: bool = False) -> bytes:
    files = []
    for path in sorted((incident / "records").glob("*.jsonl")):
        data = path.read_bytes()
        files.append({"path": f"records/{path.name}", "kind": "records", "retained": True,
                      "bytes": len(data), "finalized": closed,
                      "sha256": hashlib.sha256(data).hexdigest() if closed else None})
    payload = json.dumps({"schema": "eg4_host_incident.v1", "incident_id": "synthetic",
                          "session_id": "synthetic-session", "entry_id": ENTRY,
                          "state": "closed" if closed else "open", "complete": closed,
                          "files": files, "trigger_files": [], "trigger_count": 0}).encode()
    (incident / "manifest.json").write_bytes(payload)
    return payload


# Execute the real operation, then kill the child before Python can save metadata
# or run finally blocks. Wrappers cover either rename or no-clobber link publication.
_CRASH = r'''
import os, sys
from datetime import datetime
from pathlib import Path
from scripts.eg4_archive import LocalSource, archive_once
source, destination, boundary, clock, entry = sys.argv[1:]
def die(path):
    # This is an external test receipt, never an archive-owned pathname.
    Path(destination + ".crash").write_text(str(path))
    os._exit(73)
original_open = os.open
def opened(path, flags, *args, **kwargs):
    fd = original_open(path, flags, *args, **kwargs)
    text = str(path)
    if flags & os.O_CREAT and boundary == "create" and text.endswith(".jsonl"):
        die(path)
    if flags & os.O_CREAT and boundary == "stage" and (
            ".archive-create-" in text or text.endswith(".jsonl")):
        die(path)
    return fd
os.open = opened
for operation in ("rename", "replace", "link"):
    original = getattr(os, operation)
    def published(src, dst, *args, _original=original, **kwargs):
        result = _original(src, dst, *args, **kwargs)
        text = str(dst)
        if boundary in ("create", "promote", "incident") and text.endswith(".jsonl"):
            die(dst)
        return result
    setattr(os, operation, published)
original_unlink = os.unlink
def unlinked(path, *args, **kwargs):
    result = original_unlink(path, *args, **kwargs)
    if boundary == "prune" and str(path).endswith(".jsonl"):
        die(path)
    return result
os.unlink = unlinked
original_sync = os.fsync
def synced(fd):
    result = original_sync(fd)
    name = os.readlink(f"/proc/self/fd/{fd}")
    if boundary == "bootstrap" and (name.endswith(".archive.lock") or
            (".archive.json-" in name and name.endswith(".tmp"))):
        die(name)
    return result
os.fsync = synced
archive_once(LocalSource(Path(source)), Path(destination), entry_id=entry,
             min_free_bytes=0, now=datetime.fromisoformat(clock))
'''


def _crash(source: Path, destination: Path, boundary: str, *, days: int = 0) -> Path:
    result = subprocess.run([sys.executable, "-c", _CRASH, str(source), str(destination),
                             boundary, (WHEN + timedelta(days=days)).isoformat(), ENTRY],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 73, result.stderr
    return Path(Path(str(destination) + ".crash").read_text())


@pytest.mark.parametrize("boundary", ["bootstrap", "stage", "create", "promote", "prune", "incident"])
def test_process_death_restarts_without_losing_or_duplicating_bytes(tmp_path: Path, boundary: str) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    first = b'{"id":"first"}\n'
    second = b'{"id":"second"}\n'
    active = source / "raw" / "local-dongle.jsonl"
    active.write_bytes(first)
    if boundary == "promote":
        active.write_bytes(first[:8])
        _run(source, destination)
        active.write_bytes(first)
    elif boundary == "prune":
        _run(source, destination)
    elif boundary == "incident":
        incident = _incident(source, first)
        _run(source, destination)
        moving = incident / "records" / "local-dongle.jsonl"
        moving.rename(moving.with_name("local-dongle-123.jsonl"))
        moving.write_bytes(second)
    _crash(source, destination, boundary, days=9 if boundary == "prune" else 0)
    status = _run(source, destination, days=9 if boundary == "prune" else 0)
    assert status["state"] == "healthy"
    raw = [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")]
    assert raw == ([] if boundary == "prune" else [first])
    if boundary == "incident":
        captured = destination / "incidents" / "incident-synthetic" / "records"
        assert sorted(path.read_bytes() for path in captured.glob("*.jsonl")) == sorted([first, second])


@pytest.mark.parametrize("boundary", ["create", "promote", "prune", "incident"])
def test_recovery_never_adopts_or_unlinks_replacement_inode(tmp_path: Path, boundary: str) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    active.write_bytes(b'{"id":"first"}\n')
    if boundary == "promote":
        active.write_bytes(b'{"id":')
        _run(source, destination)
        active.write_bytes(b'{"id":"first"}\n')
    elif boundary == "prune":
        _run(source, destination)
    elif boundary == "incident":
        incident = _incident(source, b'{"id":"old"}\n')
        _run(source, destination)
        moving = incident / "records" / "local-dongle.jsonl"
        moving.rename(moving.with_name("local-dongle-123.jsonl"))
        moving.write_bytes(b'{"id":"new"}\n')
    published = _crash(source, destination, boundary, days=9 if boundary == "prune" else 0)
    # Keep the old inode allocated, avoiding accidental inode-number reuse.
    if published.exists():
        published.rename(tmp_path / "original-inode")
    published.write_bytes(b"UNRELATED REPLACEMENT\n")
    with pytest.raises(ArchiveError):
        _run(source, destination, days=9 if boundary == "prune" else 0)
    assert published.read_bytes() == b"UNRELATED REPLACEMENT\n"
    owner = json.loads((destination / "archive.json").read_bytes())
    assert published.stat().st_ino not in [claim.get("inode") for claim in owner["segments"].values()]


def test_incident_partial_first_record_rotates_without_duplicate_prefix(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    first = b'{"kind":"transaction","id":"first"}\n'
    second = b'{"kind":"transaction","id":"second"}\n'
    incident = _incident(source, first[:-3])
    _run(source, destination)
    active = incident / "records" / "local-dongle.jsonl"
    active.write_bytes(first)
    active.rename(active.with_name("local-dongle-123.jsonl"))
    active.write_bytes(second)
    _run(source, destination)
    copied = destination / "incidents" / incident.name / "records"
    assert sorted(path.read_bytes() for path in copied.glob("*.jsonl")) == sorted([first, second])
    assert _run(source, destination)["bytes_copied"] == 0


def test_incident_unmatched_partial_evidence_survives_active_replacement(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    partial = b'{"kind":"transaction","id":"lost'
    incident = _incident(source, partial)
    _run(source, destination)
    replacement = b'{"kind":"transaction","id":"replacement"}\n'
    (incident / "records" / "local-dongle.jsonl").write_bytes(replacement)
    _run(source, destination)
    copied = destination / "incidents" / incident.name / "records"
    assert sorted(path.read_bytes() for path in copied.glob("*.jsonl")) == sorted([partial, replacement])


def _analyze(incident: Path) -> str:
    result = subprocess.run([sys.executable, str(ROOT / "scripts/analyze_local_dongle_trace.py"),
                             str(incident)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _uncheckpointed_evidence() -> bytes:
    # Native transactions preserve wire outcomes; decoded telemetry belongs to
    # acquisition rows. Both the pinned and working-tree analyzers enforce this.
    rows = [
        {"schema": "eg4_local_dongle_trace.v2", "kind": "transaction", "role": "master",
         "ok": False, "transaction_id": "unique-uncheckpointed-wire",
         "request": {"function": 4, "register": 0, "count": 40},
         "frames": [{"partial": True, "bytes_hex": "a11a0100"}]},
        {"schema": "eg4_local_dongle_trace.v2", "kind": "acquisition", "role": "master",
         "ok": True, "acquisition_id": "unique-uncheckpointed-acquisition",
         "telemetry": {"core": {"eps_voltage_r_v": 282.9, "eps_voltage_s_v": 120}}},
    ]
    return b"".join((json.dumps(row) + "\n").encode() for row in rows)


def test_uncheckpointed_and_later_pruned_incident_evidence_remains_analyzable(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    event = _uncheckpointed_evidence()
    incident = _incident(source, b'{"kind":"transaction","role":"gridboss","ok":false}\n')
    original = (incident / "manifest.json").read_bytes()
    (incident / "records" / "local-dongle-123.jsonl").write_bytes(event)
    (incident / "triggers").mkdir()
    trigger = b'{"kind":"incident_trigger","reason":"unique-archive-trigger"}\n'
    (incident / "triggers" / "local-dongle.jsonl").write_bytes(trigger)
    _run(source, destination)
    archived = destination / "incidents" / incident.name
    analysis = _analyze(archived)
    assert "282.9" in analysis
    assert "unique-uncheckpointed-wire" in analysis
    assert "unique-archive-trigger" in analysis
    manifest = json.loads((archived / "manifest.json").read_bytes())
    assert manifest["complete"] is False
    assert manifest["trigger_count"] == 1
    assert manifest["trigger_files"] == ["triggers/local-dongle.jsonl"]
    assert (archived / "source-manifest.json").read_bytes() == original
    for entry in manifest["files"]:
        data = (archived / entry["path"]).read_bytes()
        assert entry["retained"] is True
        assert entry["bytes"] == len(data)
        assert entry["finalized"] is False
        assert entry["sha256"] is None
    shutil.rmtree(incident)
    _run(source, destination)
    analysis = _analyze(archived)
    assert "282.9" in analysis
    assert "unique-uncheckpointed-wire" in analysis
    assert "unique-archive-trigger" in analysis
    assert (archived / "records" / "local-dongle-123.jsonl").read_bytes() == event
    receipt = json.loads((archived / "archive-receipt.json").read_bytes())
    assert receipt["received_complete"] is False
    assert receipt["source_available"] is False


def test_sealing_retains_unlisted_previously_received_evidence_and_both_manifest_hashes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source, b'{"id":"active"}\n')
    lost = _uncheckpointed_evidence()
    rotation = incident / "records" / "local-dongle-123.jsonl"
    rotation.write_bytes(lost)
    _run(source, destination)
    rotation.unlink()
    original_final = _manifest(incident, closed=True)
    _run(source, destination)
    archived = destination / "incidents" / incident.name
    analysis = _analyze(archived)
    assert "282.9" in analysis
    assert "unique-uncheckpointed-wire" in analysis
    assert (archived / "source-manifest.json").read_bytes() == original_final
    derived = (archived / "manifest.json").read_bytes()
    manifest = json.loads(derived)
    assert manifest["complete"] is False
    assert manifest["archive_provenance"]["source_complete"] is True
    receipt = json.loads((archived / "archive-receipt.json").read_bytes())
    assert receipt["state"] == "sealed"
    assert receipt["received_complete"] is False
    assert receipt["manifest_sha256"] == hashlib.sha256(original_final).hexdigest()
    assert receipt["archive_manifest_sha256"] == hashlib.sha256(derived).hexdigest()
    assert (archived / "records" / rotation.name).read_bytes() == lost
    shutil.rmtree(incident)
    _run(source, destination, days=100)
    assert (archived / "manifest.json").read_bytes() == derived
    assert "unique-uncheckpointed-wire" in _analyze(archived)


def test_transfer_exception_carries_the_written_degraded_receipt(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    (source / "raw" / "a.jsonl").write_bytes(b'{"id":"copied"}\n')
    # A later invalid incident fails after copying real bytes, not before source trust.
    incident = _incident(source, b'{"id":"incident"}\n')
    (incident / "manifest.json").write_text('{"schema":"wrong"}')
    with pytest.raises(ArchiveError) as caught:
        _run(source, destination)
    written = json.loads((destination / "status.json").read_bytes())
    assert written["bytes_copied"] > 0
    assert caught.value.status == written
    assert written["state"] == "degraded"
    with pytest.raises(ArchiveError) as prelock:
        _run(source, tmp_path)
    assert prelock.value.status is None


def test_pre_intent_stage_is_not_adopted_or_removed_on_restart(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    expected = b'{"id":"source-only"}\n'
    (source / "raw" / "local-dongle.jsonl").write_bytes(expected)
    stage = _crash(source, destination, "stage")
    stage.rename(tmp_path / "unindexed-empty-inode")
    stage.write_bytes(b"UNRELATED STAGE REPLACEMENT\n")
    _run(source, destination)
    assert stage.read_bytes() == b"UNRELATED STAGE REPLACEMENT\n"
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [expected]
