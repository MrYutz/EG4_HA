"""Exercise durable off-host preservation through the approved public seam."""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from scripts.eg4_archive import (
    ArchiveError,
    LocalSource,
    SourceRead,
    archive_once,
    record_failure,
)


def test_raw_prefix_survives_source_rotation_and_local_eviction(tmp_path: Path) -> None:
    """Rotating/reusing the HA active name cannot overwrite archived observations."""
    source = tmp_path / "source"
    raw = source / "raw"
    raw.mkdir(parents=True)
    (source / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": "synthetic-archive",
    }))
    archive = tmp_path / "archive"
    when = datetime(2026, 11, 10, 12, tzinfo=UTC)
    first = {
        "schema": "eg4_local_dongle_trace.v2", "kind": "transaction",
        "session_id": "synthetic-session", "role": "gridboss",
        "acquisition_id": "first-source", "transaction_id": "first-wire",
        "persistence_started_at": when.isoformat(), "started_at": when.isoformat(),
        "completed_at": when.isoformat(), "ok": False,
        "request": {"function": 4, "register": 0, "count": 40},
        "frames": [{"partial": True, "bytes_hex": "a11a0100"}],
    }
    active = raw / "local-dongle.jsonl"
    original = json.dumps(first).encode() + b"\n"
    active.write_bytes(original)
    archive_once(LocalSource(source), archive, entry_id="synthetic-archive", now=when, min_free_bytes=0)
    rotated = raw / "local-dongle-1794312001000000000.jsonl"
    active.rename(rotated)
    second = {**first, "acquisition_id": "second-source", "transaction_id": "second-wire",
              "persistence_started_at": (when + timedelta(seconds=1)).isoformat()}
    active.write_text(json.dumps(second) + "\n")
    archive_once(LocalSource(source), archive, entry_id="synthetic-archive", now=when + timedelta(seconds=2), min_free_bytes=0)
    rotated.unlink()
    archive_once(LocalSource(source), archive, entry_id="synthetic-archive", now=when + timedelta(days=7), min_free_bytes=0)
    archived = [path.read_bytes() for path in (archive / "raw").glob("*.jsonl")]
    assert original in archived
    rows = [json.loads(line) for data in archived for line in data.splitlines()]
    assert sorted(row["transaction_id"] for row in rows) == ["first-wire", "second-wire"]
    assert next(row for row in rows if row["transaction_id"] == "first-wire")["frames"][0]["bytes_hex"] == "a11a0100"


ENTRY = "synthetic-archive"
WHEN = datetime(2026, 11, 10, 12, tzinfo=UTC)


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for directory in ("raw", "controller-observations", "incidents"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": ENTRY,
    }))
    return root


def _run(source: Path, destination: Path, *, days: float = 0) -> dict:
    return archive_once(LocalSource(source), destination, entry_id=ENTRY,
                        min_free_bytes=0, now=WHEN + timedelta(days=days))


def _json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _incident(source: Path, *, state: str = "open", complete: bool = False) -> Path:
    root = source / "incidents" / "incident-synthetic"
    (root / "records").mkdir(parents=True)
    (root / "records" / "local-dongle.jsonl").write_bytes(b'{"kind":"transaction","id":"first"}\n')
    _manifest(root, state=state, complete=complete)
    return root


def _manifest(root: Path, *, state: str, complete: bool) -> None:
    entries = []
    for path in sorted((root / "records").glob("*.jsonl")):
        data = path.read_bytes()
        entry = {"path": f"records/{path.name}", "kind": "records", "retained": True,
                 "bytes": len(data), "finalized": state == "closed"}
        if state == "closed":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
        entries.append(entry)
    (root / "manifest.json").write_text(json.dumps({
        "schema": "eg4_host_incident.v1", "incident_id": "synthetic",
        "session_id": "synthetic-session", "entry_id": ENTRY,
        "state": state, "complete": complete, "files": entries, "trigger_files": [],
    }))


def test_incremental_raw_and_journal_preserve_opaque_partial_bytes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    raw = b'{"id":"raw"}\n\xff\x00partial'
    journal = b'{"id":"controller"}\nunfinished'
    for directory, data in (("raw", raw), ("controller-observations", journal)):
        (source / directory / "local-dongle.jsonl").write_bytes(data)
    first = _run(source, destination)
    assert first["bytes_copied"] == len(raw) + len(journal)
    assert first["files_changed"] == 2
    for directory in ("raw", "controller-observations"):
        with (source / directory / "local-dongle.jsonl").open("ab") as stream:
            stream.write(b"\x01\xfe\n")
    second = _run(source, destination, days=1)
    assert second["bytes_copied"] == 6
    assert second["raw_bytes"] == len(raw) + 3
    assert second["journal_bytes"] == len(journal) + 3
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [raw + b"\x01\xfe\n"]
    assert [path.read_bytes() for path in (destination / "controller-observations").glob("*.jsonl")] == [journal + b"\x01\xfe\n"]
    unchanged = _run(source, destination, days=2)
    assert unchanged["bytes_copied"] == unchanged["files_changed"] == 0


def test_retention_boundary_leaves_recent_and_unrelated_files(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    old = b'{"id":"old"}\n'
    active.write_bytes(old)
    _run(source, destination)
    active.unlink()
    assert _run(source, destination, days=8)["raw_files"] == 1
    active.write_bytes(b'{"id":"recent"}\n')
    unrelated = destination / "raw" / "operator-notes.jsonl"
    unrelated.write_bytes(b"not owned by the archive\n")
    result = _run(source, destination, days=8.01)
    assert result["pruned_files"] == 1
    assert result["raw_files"] == 1
    assert unrelated.read_bytes() == b"not owned by the archive\n"
    assert old not in [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")]
    assert (destination / "raw").is_dir()


@pytest.mark.parametrize("directory", ["raw", "controller-observations"])
def test_retention_tracks_new_bytes_not_polling_and_does_not_recopy_expired_prefix(
    tmp_path: Path, directory: str,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / directory / "local-dongle.jsonl"
    active.write_bytes(b'{"id":"first"}\n')
    _run(source, destination)
    with active.open("ab") as stream:
        stream.write(b'{"id":"later"}\n')
    _run(source, destination, days=7)
    assert len(list((destination / directory).glob("*.jsonl"))) == 1
    _run(source, destination, days=14.99)
    expired = _run(source, destination, days=15.01)
    assert expired["pruned_files"] == 1
    assert not list((destination / directory).glob("*.jsonl"))
    unchanged = _run(source, destination, days=15.02)
    assert unchanged["bytes_copied"] == 0
    assert not list((destination / directory).glob("*.jsonl"))
    with active.open("ab") as stream:
        stream.write(b'{"id":"newly-available"}\n')
    restored = _run(source, destination, days=15.03)
    assert restored["bytes_copied"] == active.stat().st_size
    assert [path.read_bytes() for path in (destination / directory).glob("*.jsonl")] == [active.read_bytes()]


@pytest.mark.parametrize("replacement", [b"", b'{"id":"first"}\n', b'{"id":"first"}\n{"id":"WRONG"}\n'])
def test_source_shrink_or_changed_prefix_never_truncates_history(
    tmp_path: Path, replacement: bytes,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    original = b'{"id":"first"}\n{"id":"second"}\n'
    active.write_bytes(original)
    success = _run(source, destination)
    active.write_bytes(replacement)
    with pytest.raises(ArchiveError):
        _run(source, destination, days=20)
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [original]
    status = _json(destination / "status.json")
    assert status["state"] == "degraded"
    assert status["last_success_at"] == success["last_success_at"]
    assert status["bytes_copied"] == 0


def test_source_identity_change_cannot_replace_marker_or_expire_prior_evidence(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    data = b'{"id":"original"}\n'
    (source / "raw" / "local-dongle.jsonl").write_bytes(data)
    _run(source, destination)
    marker = (destination / "recorder.json").read_bytes()
    (source / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": "someone-else",
    }))
    with pytest.raises(ArchiveError):
        _run(source, destination, days=100)
    assert (destination / "recorder.json").read_bytes() == marker
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [data]
    assert _json(destination / "status.json")["state"] == "degraded"


def test_source_loss_and_connection_failure_preserve_last_success_and_private_metadata(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    data = b'{"id":"preserved"}\n'
    (source / "raw" / "local-dongle.jsonl").write_bytes(data)
    success = _run(source, destination)
    shutil.rmtree(source)
    with pytest.raises(ArchiveError):
        _run(source, destination, days=1)
    status = record_failure(destination, entry_id=ENTRY, error="SSH connection unavailable",
                            now=WHEN + timedelta(days=2))
    assert status["state"] == "degraded"
    assert status["last_success_at"] == success["last_success_at"]
    assert status["raw_files"] == 1
    assert status["raw_bytes"] == len(data)
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [data]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    for name in ("status.json", "archive.json", "recorder.json", ".archive.lock"):
        assert stat.S_IMODE((destination / name).stat().st_mode) == 0o600


def test_insufficient_space_fails_without_evicting_recent_history(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    original = b'{"id":"first"}\n'
    active.write_bytes(original)
    _run(source, destination)
    with active.open("ab") as stream:
        stream.write(b'{"id":"more"}\n')
    with pytest.raises(ArchiveError):
        archive_once(LocalSource(source), destination, entry_id=ENTRY, min_free_bytes=2**63,
                     now=WHEN + timedelta(days=1))
    assert [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")] == [original]
    assert _json(destination / "status.json")["state"] == "degraded"
    resumed = _run(source, destination, days=2)
    assert resumed["bytes_copied"] == len(b'{"id":"more"}\n')


def test_first_partial_record_completion_and_rotation_do_not_duplicate_bytes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    active.write_bytes(b'{"id":"first')
    _run(source, destination)
    with active.open("ab") as stream:
        stream.write(b'-record"}\n{"id":"second"}\n')
    _run(source, destination, days=1)
    original = active.read_bytes()
    active.rename(active.with_name("local-dongle-123.jsonl"))
    active.write_bytes(b'{"id":"replacement"}\n')
    _run(source, destination, days=2)
    archived = [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")]
    assert sorted(archived) == sorted([original, b'{"id":"replacement"}\n'])


def test_backfill_filenames_order_by_original_persistence_interval(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    earlier = b'{"persistence_started_at":"2026-11-01T00:00:00Z","id":"earlier"}\n'
    later = b'{"persistence_started_at":"2026-11-02T00:00:00Z","id":"later"}\n'
    (source / "raw" / "local-dongle.jsonl").write_bytes(earlier)
    (source / "raw" / "local-dongle-123.jsonl").write_bytes(later)
    _run(source, destination)
    assert [path.read_bytes() for path in sorted((destination / "raw").glob("*.jsonl"))] == [earlier, later]


@pytest.mark.parametrize("state,complete", [("closed", True), ("interrupted", False)])
def test_sealed_incident_is_indefinite_and_ignores_later_source_changes(
    tmp_path: Path, state: str, complete: bool,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source, state=state, complete=complete)
    result = _run(source, destination)
    archived = destination / "incidents" / incident.name
    before = {path.relative_to(archived): path.read_bytes() for path in archived.rglob("*") if path.is_file()}
    receipt = _json(archived / "archive-receipt.json")
    assert result["sealed_incidents"] == 1
    assert receipt["state"] == "sealed"
    assert receipt["source_complete"] is complete
    assert receipt["received_complete"] is complete
    (incident / "records" / "local-dongle.jsonl").write_bytes(b"source changed after custody\n")
    (incident / "manifest.json").write_bytes(b"not a manifest anymore")
    assert _run(source, destination, days=1)["sealed_incidents"] == 1
    shutil.rmtree(incident)
    assert _run(source, destination, days=100)["sealed_incidents"] == 1
    after = {path.relative_to(archived): path.read_bytes() for path in archived.rglob("*") if path.is_file()}
    assert after == before


def test_final_hash_mismatch_preserves_received_bytes_but_never_seals(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source, state="closed", complete=True)
    manifest = _json(incident / "manifest.json")
    manifest["files"][0]["sha256"] = "0" * 64
    (incident / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ArchiveError):
        _run(source, destination)
    archived = destination / "incidents" / incident.name
    assert (archived / "records" / "local-dongle.jsonl").read_bytes() == (incident / "records" / "local-dongle.jsonl").read_bytes()
    receipt = _json(archived / "archive-receipt.json")
    assert receipt["state"] == "moving"
    assert receipt["received_complete"] is False
    assert _json(destination / "status.json")["sealed_incidents"] == 0
    _manifest(incident, state="closed", complete=True)
    assert _run(source, destination, days=1)["sealed_incidents"] == 1


def test_open_incident_mirrors_uncheckpointed_rotation_and_keeps_pruned_prefixes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source)
    active = incident / "records" / "local-dongle.jsonl"
    first = active.read_bytes()
    _run(source, destination)
    active.rename(active.with_name("local-dongle-123.jsonl"))
    second = b'{"kind":"transaction","id":"second"}\n'
    active.write_bytes(second)
    # Leave the open checkpoint stale: the rotated file is deliberately unlisted.
    _run(source, destination, days=1)
    archived = destination / "incidents" / incident.name
    assert sorted(path.read_bytes() for path in (archived / "records").glob("*.jsonl")) == sorted([first, second])
    manifest = (archived / "manifest.json").read_bytes()
    shutil.rmtree(incident)
    result = _run(source, destination, days=100)
    receipt = _json(archived / "archive-receipt.json")
    assert result["sealed_incidents"] == 0
    assert receipt["state"] == "moving"
    assert receipt["source_available"] is False
    assert receipt["received_complete"] is False
    assert receipt["error"] == "source_missing"
    assert (archived / "manifest.json").read_bytes() == manifest
    assert sorted(path.read_bytes() for path in (archived / "records").glob("*.jsonl")) == sorted([first, second])


@pytest.mark.parametrize("unsafe", ["../outside.jsonl", "/outside.jsonl", "records/../../outside.jsonl", "records\\outside.jsonl"])
def test_unsafe_incident_reference_cannot_escape_or_seal(tmp_path: Path, unsafe: str) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source, state="closed", complete=True)
    manifest = _json(incident / "manifest.json")
    manifest["files"][0]["path"] = unsafe
    (incident / "manifest.json").write_text(json.dumps(manifest))
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"unrelated evidence")
    with pytest.raises(ArchiveError):
        _run(source, destination)
    assert outside.read_bytes() == b"unrelated evidence"
    assert _json(destination / "status.json")["sealed_incidents"] == 0


def test_source_symlink_and_destination_symlink_are_never_followed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"private unrelated bytes")
    (source / "raw" / "local-dongle.jsonl").symlink_to(outside)
    destination = tmp_path / "archive"
    with pytest.raises(ArchiveError):
        _run(source, destination)
    assert not list((destination / "raw").glob("*.jsonl"))
    assert outside.read_bytes() == b"private unrelated bytes"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "linked-destination"
    link.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ArchiveError):
        _run(source, link)
    assert not list(elsewhere.iterdir())


def test_replaced_archived_inode_is_not_owned_for_retention(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    active.write_bytes(b'{"id":"first"}\n')
    _run(source, destination)
    archived = next((destination / "raw").glob("*.jsonl"))
    unrelated = destination / "replacement"
    unrelated.write_bytes(b"unrelated replacement inode\n")
    unrelated.replace(archived)
    active.unlink()
    with pytest.raises(ArchiveError):
        _run(source, destination, days=100)
    assert archived.read_bytes() == b"unrelated replacement inode\n"
    assert _json(destination / "status.json")["state"] == "degraded"


def test_real_file_read_interruption_keeps_synced_prefix_and_resumes_without_duplication(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    data = b'{"id":"interrupted"}\n' + b"\x00\xffopaque\n" * 150_000
    (source / "raw" / "local-dongle.jsonl").write_bytes(data)

    class CutoffReader:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream
            self.remaining = 900 * 1024

        def read(self, size: int = -1) -> bytes:
            if self.remaining == 0:
                raise OSError("synthetic transport disconnected during a real file read")
            chunk = self.stream.read(min(size, self.remaining))
            self.remaining -= len(chunk)
            return chunk

        def seek(self, offset: int) -> int:
            return self.stream.seek(offset)

    class InterruptedSource(LocalSource):
        @contextmanager
        def open(self, relative: str):
            with super().open(relative) as opened:
                if relative.startswith("raw/"):
                    yield SourceRead(cast(BinaryIO, CutoffReader(opened.stream)), opened.size)
                else:
                    yield opened

    with pytest.raises(ArchiveError):
        archive_once(InterruptedSource(source), destination, entry_id=ENTRY,
                     min_free_bytes=0, now=WHEN)
    archived = next((destination / "raw").glob("*.jsonl"))
    prefix = archived.read_bytes()
    assert 0 < len(prefix) < len(data)
    assert data.startswith(prefix)
    failed = _json(destination / "status.json")
    assert failed["state"] == "degraded"
    assert failed["last_success_at"] is None
    assert failed["bytes_copied"] == len(prefix)
    assert failed["raw_bytes"] == len(prefix)
    resumed = _run(source, destination, days=1)
    assert resumed["bytes_copied"] == len(data) - len(prefix)
    assert archived.read_bytes() == data
    assert len(list((destination / "raw").glob("*.jsonl"))) == 1


def test_actual_archived_length_wins_over_a_stale_cursor(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    original = b'{"id":"first"}\n'
    more = b'{"id":"received-before-metadata"}\n'
    active.write_bytes(original)
    _run(source, destination)
    archived = next((destination / "raw").glob("*.jsonl"))
    # Reproduce a received, synchronized tail whose index update never committed.
    with active.open("ab") as stream:
        stream.write(more)
    with archived.open("ab") as stream:
        stream.write(more)
    resumed = _run(source, destination, days=7)
    assert resumed["bytes_copied"] == 0
    assert resumed["raw_bytes"] == len(original + more)
    assert archived.read_bytes() == original + more
    _run(source, destination, days=8.01)
    assert archived.read_bytes() == original + more


def test_existing_analyzer_reads_native_archived_stream_layout(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    raw = {
        "schema": "eg4_local_dongle_trace.v2", "kind": "transaction", "role": "gridboss",
        "session_id": "synthetic-session", "acquisition_id": "synthetic-acquisition",
        "transaction_id": "synthetic-wire", "started_at": WHEN.isoformat(),
        "completed_at": WHEN.isoformat(), "ok": False,
        "request": {"function": 4, "register": 0, "count": 40},
        "frames": [{"partial": True, "bytes_hex": "a11a0100"}],
    }
    journal = {
        "schema": "eg4_controller_observation.v1", "kind": "acquisition", "role": "master",
        "session_id": "synthetic-session", "observation": "controller_changed", "ok": False,
        "sample_started_at": WHEN.isoformat(), "sample_completed_at": WHEN.isoformat(),
        "changed_fields": ["input.6.bits_0_1"], "controller": {"input": {"6": 3}},
    }
    (source / "raw" / "local-dongle.jsonl").write_text(json.dumps(raw) + "\n")
    (source / "controller-observations" / "local-dongle.jsonl").write_text(json.dumps(journal) + "\n")
    _run(source, destination)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/analyze_local_dongle_trace.py"),
         str(destination)], capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "source_group=rolling" in result.stdout
    assert "source_group=journal" in result.stdout
    assert "synthetic-wire" in result.stdout
    assert "input.6.bits_0_1" in result.stdout


def test_large_unchanged_and_growing_segments_use_bounded_source_reads(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    active = source / "raw" / "local-dongle.jsonl"
    data = b'{"id":"large-segment"}\n' + b"\x00\xff" * (1024 * 1024)
    active.write_bytes(data)
    _run(source, destination)

    class CountingReader:
        def __init__(self, stream: BinaryIO, owner: CountingSource) -> None:
            self.stream = stream
            self.owner = owner

        def read(self, size: int = -1) -> bytes:
            chunk = self.stream.read(size)
            self.owner.bytes_read += len(chunk)
            return chunk

        def seek(self, offset: int) -> int:
            return self.stream.seek(offset)

    class CountingSource(LocalSource):
        bytes_read = 0

        @contextmanager
        def open(self, relative: str):
            with super().open(relative) as opened:
                if relative.startswith("raw/"):
                    yield SourceRead(cast(BinaryIO, CountingReader(opened.stream, self)), opened.size)
                else:
                    yield opened

    measured = CountingSource(source)
    unchanged = archive_once(measured, destination, entry_id=ENTRY, min_free_bytes=0, now=WHEN)
    assert unchanged["bytes_copied"] == 0
    assert measured.bytes_read < 128 * 1024
    suffix = b"newly received opaque bytes\n"
    with active.open("ab") as stream:
        stream.write(suffix)
    measured.bytes_read = 0
    appended = archive_once(measured, destination, entry_id=ENTRY, min_free_bytes=0,
                            now=WHEN + timedelta(days=1))
    assert appended["bytes_copied"] == len(suffix)
    assert measured.bytes_read < 128 * 1024 + len(suffix)
    assert next((destination / "raw").glob("*.jsonl")).read_bytes() == data + suffix


def test_final_source_hash_can_seal_already_received_file_after_source_pruning(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    incident = _incident(source)
    data = (incident / "records" / "local-dongle.jsonl").read_bytes()
    _run(source, destination)
    _manifest(incident, state="closed", complete=True)
    (incident / "records" / "local-dongle.jsonl").unlink()
    assert _run(source, destination, days=1)["sealed_incidents"] == 1
    archived = destination / "incidents" / incident.name
    receipt = _json(archived / "archive-receipt.json")
    assert receipt["state"] == "sealed"
    assert receipt["files"]["records/local-dongle.jsonl"]["source_final_hash_verified"] is True
    assert receipt["files"]["records/local-dongle.jsonl"]["source_available"] is False
    assert (archived / "records" / "local-dongle.jsonl").read_bytes() == data
