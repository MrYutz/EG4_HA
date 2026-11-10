"""File-only, opaque-byte custody for the existing EG4 recorder layout."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, ContextManager, Protocol
from uuid import uuid4

_CHUNK = 256 * 1024
_METADATA_LIMIT = 16 * 1024**2
_STREAMS = ("raw", "controller-observations")
_INCIDENT_NAME = re.compile(r"incident-[A-Za-z0-9_-]+\Z")


class ArchiveError(Exception):
    """A transfer or custody check failed; existing evidence is not disposable."""


@dataclass(frozen=True)
class SourceEntry:
    name: str
    is_dir: bool
    is_file: bool


@dataclass(frozen=True)
class SourceRead:
    stream: BinaryIO
    size: int


class Source(Protocol):
    def listdir(self, relative: str) -> list[SourceEntry]: ...

    def open(self, relative: str) -> ContextManager[SourceRead]: ...


def _parts(relative: str, *, empty: bool = False) -> tuple[str, ...]:
    if empty and relative == "":
        return ()
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ArchiveError(f"unsafe relative path: {relative!r}")
    parts = tuple(relative.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ArchiveError(f"unsafe relative path: {relative!r}")
    return parts


def _directory(path: Path, *, create: bool = False) -> int:
    """Walk every component with no-follow, including the archive's ancestors."""
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise ArchiveError(f"unsafe directory: {path}")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class LocalSource:
    """Read regular local source files without following any directory symlinks."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, relative: str) -> int:
        parts = _parts(relative, empty=True)
        return _directory(self.root.joinpath(*parts))

    def listdir(self, relative: str) -> list[SourceEntry]:
        descriptor = self._dir(relative)
        try:
            with os.scandir(descriptor) as entries:
                result = []
                for entry in entries:
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        raise ArchiveError(f"source symlink rejected: {relative}/{entry.name}")
                    result.append(SourceEntry(entry.name, stat.S_ISDIR(mode), stat.S_ISREG(mode)))
                return result
        finally:
            os.close(descriptor)

    @contextmanager
    def open(self, relative: str) -> Iterator[SourceRead]:
        parts = _parts(relative)
        directory = self._dir("/".join(parts[:-1]))
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ArchiveError(f"not a regular source file: {relative}")
            yield SourceRead(stream, info.st_size)


def _time(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArchiveError("now must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _date(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ArchiveError("invalid archive timestamp")
    return _time(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _document(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ArchiveError("metadata must be a JSON object")
    return value


def _read_bytes(opened: SourceRead, limit: int = _METADATA_LIMIT) -> bytes:
    if type(opened.size) is not int or not 0 <= opened.size <= limit:
        raise ArchiveError("invalid or excessive metadata size")
    result = bytearray()
    while len(result) < opened.size:
        chunk = opened.stream.read(min(_CHUNK, opened.size - len(result)))
        if not chunk:
            raise ArchiveError("source ended before its advertised size")
        result.extend(chunk)
    return bytes(result)


def _source_metadata(source: Source, relative: str) -> tuple[dict[str, Any], bytes]:
    with source.open(relative) as opened:
        payload = _read_bytes(opened)
    return _document(payload), payload


def _entries(source: Source, relative: str) -> dict[str, SourceEntry]:
    result = {}
    for entry in source.listdir(relative):
        if len(_parts(entry.name)) != 1 or entry.name in result:
            raise ArchiveError(f"unsafe or duplicate source entry: {entry.name!r}")
        if entry.is_dir == entry.is_file:
            raise ArchiveError(f"unsupported source entry: {relative}/{entry.name}")
        result[entry.name] = entry
    return result


def _first_record(opened: SourceRead) -> dict[str, Any]:
    """Identify a segment, not decode observations; retain even non-JSON bytes."""
    if type(opened.size) is not int or opened.size < 0:
        raise ArchiveError("invalid source file size")
    digest = hashlib.sha256()
    sample = bytearray()
    length = 0
    complete = False
    while length < opened.size:
        chunk = opened.stream.read(min(4096, opened.size - length))
        if not chunk:
            raise ArchiveError("source ended during segment identification")
        newline = chunk.find(b"\n")
        if newline >= 0:
            chunk = chunk[:newline + 1]
            complete = True
        digest.update(chunk)
        length += len(chunk)
        if len(sample) + len(chunk) <= 1024**2:
            sample.extend(chunk)
        else:
            sample.clear()
        if complete:
            break
    interval = None
    if complete and length <= 1024**2:
        try:
            row = _document(bytes(sample))
            for key in ("persistence_started_at", "started_at", "observed_at", "written_at"):
                if row.get(key):
                    interval = _date(row[key])
                    break
        except (ValueError, TypeError, ArchiveError):
            pass
    opened.stream.seek(0)
    return {"anchor_bytes": length, "anchor_sha256": digest.hexdigest(),
            "first_record_sha256": digest.hexdigest() if complete else None,
            "interval": interval}


def _prefix_hash(opened: SourceRead, length: int) -> str:
    opened.stream.seek(0)
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        chunk = opened.stream.read(min(_CHUNK, remaining))
        if not chunk:
            raise ArchiveError("source shrank during prefix verification")
        digest.update(chunk)
        remaining -= len(chunk)
    opened.stream.seek(0)
    return digest.hexdigest()

class _Archive:
    def __init__(self, destination: Path, entry_id: str, now: datetime, *, controlled_clock: bool) -> None:
        if not isinstance(entry_id, str) or not entry_id:
            raise ArchiveError("entry_id must be nonempty")
        self.root = Path(destination).absolute()
        self.entry_id = entry_id
        self.now = now
        self.controlled_clock = controlled_clock
        self.owner: dict[str, Any] = {}
        self.previous: dict[str, Any] = {}
        self.bytes_copied = 0
        self.changed: set[str] = set()
        self.pruned_files = 0
        self.seen_segments: set[str] = set()
        self.min_free = 0
        self.lock = -1
        self.directory = -1

    def __enter__(self) -> _Archive:
        self.directory = _directory(self.root, create=True)
        try:
            children = os.listdir(self.directory)
            if "archive.json" not in children and any(name != ".archive.lock" for name in children):
                raise ArchiveError("refusing nonempty destination without archive ownership marker")
            self.lock = os.open(".archive.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                0o600, dir_fd=self.directory)
            if not stat.S_ISREG(os.fstat(self.lock).st_mode):
                raise ArchiveError("archive lock is not a regular file")
            os.fchmod(self.lock, 0o600)
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ArchiveError("another archive transfer owns the destination lock") from error
            children = os.listdir(self.directory)
            if "archive.json" not in children and any(name != ".archive.lock" for name in children):
                raise ArchiveError("refusing nonempty destination without archive ownership marker")
            if "archive.json" in children:
                self.owner = self.load("archive.json")
                if self.owner.get("schema") != "eg4_archive.v1" or self.owner.get("entry_id") != self.entry_id:
                    raise ArchiveError("archive ownership/source entry mismatch")
                if not isinstance(self.owner.get("segments"), dict) or not isinstance(self.owner.get("incidents"), dict):
                    raise ArchiveError("invalid archive ownership index")
            else:
                self.owner = {"schema": "eg4_archive.v1", "entry_id": self.entry_id,
                              "created_at": _iso(self.now), "segments": {}, "incidents": {}}
                self.save()
            os.fchmod(self.directory, 0o700)
            for name, claim in self.owner["incidents"].items():
                if len(_parts(name)) != 1:
                    raise ArchiveError("unsafe incident index path")
                try:
                    receipt = self.load(f"incidents/{name}/archive-receipt.json")
                except FileNotFoundError:
                    continue
                claim["source_available"] = receipt.get("source_available")
                if receipt.get("state") == "sealed":
                    if (receipt.get("schema") != "eg4_archive_incident.v1"
                            or receipt.get("entry_id") != self.entry_id
                            or receipt.get("incident_id") != claim.get("incident_id")):
                        raise ArchiveError("sealed incident receipt identity mismatch")
                    claim["sealed"] = True
            for name in (*_STREAMS, "incidents"):
                self.mkdir(name)
            try:
                self.previous = self.load("status.json")
            except FileNotFoundError:
                pass
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_: Any) -> None:
        if self.lock >= 0:
            os.close(self.lock)
            self.lock = -1
        if self.directory >= 0:
            os.close(self.directory)
            self.directory = -1

    def path(self, relative: str) -> Path:
        parts = _parts(relative)
        parent = _directory(self.root.joinpath(*parts[:-1]))
        os.close(parent)
        result = self.root.joinpath(*parts)
        try:
            if stat.S_ISLNK(result.lstat().st_mode):
                raise ArchiveError(f"destination symlink rejected: {relative}")
        except FileNotFoundError:
            pass
        return result

    def mkdir(self, relative: str) -> None:
        _parts(relative)
        descriptor = _directory(self.root / relative, create=True)
        os.fchmod(descriptor, 0o700)
        os.close(descriptor)

    def load(self, relative: str) -> dict[str, Any]:
        path = self.path(relative)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ArchiveError(f"invalid archive metadata: {relative}")
            return _document(_read_bytes(SourceRead(stream, info.st_size)))

    def atomic(self, relative: str, payload: bytes) -> None:
        target = self.path(relative)
        temporary = target.with_name(f".{target.name}-{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self.sync_dir(target.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def json(self, relative: str, document: dict[str, Any]) -> None:
        self.atomic(relative, (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())

    def save(self) -> None:
        self.json("archive.json", self.owner)

    @staticmethod
    def sync_dir(path: Path) -> None:
        descriptor = _directory(path)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def space(self, size: int) -> None:
        if shutil.disk_usage(self.root).free - size < self.min_free:
            raise ArchiveError("archive free-space reserve prevents copying; recent evidence retained")

    def owned_info(self, relative: str, claim: dict[str, Any]) -> os.stat_result:
        info = self.path(relative).lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (claim.get("device"), claim.get("inode")):
            raise ArchiveError(f"archive file ownership mismatch: {relative}")
        return info

    def create(self, relative: str, claim: dict[str, Any]) -> None:
        descriptor = os.open(self.path(relative), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            claim.update(device=info.st_dev, inode=info.st_ino, bytes=0)
            os.fsync(descriptor)
            self.sync_dir((self.root / relative).parent)
            self.save()
        finally:
            os.close(descriptor)

    def file_hash(self, relative: str, claim: dict[str, Any]) -> tuple[int, str]:
        info = self.owned_info(relative, claim)
        descriptor = os.open(self.path(relative), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise ArchiveError(f"archive identity changed: {relative}")
            digest = _prefix_hash(SourceRead(stream, before.st_size), before.st_size)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ArchiveError(f"archive file changed while hashing: {relative}")
        return after.st_size, digest

    def append(self, opened: SourceRead, relative: str, claim: dict[str, Any]) -> int:
        """Validate identity and a bounded resume tail, then append new bytes.

        The trusted recorder only appends or rotates. Historical middle rewrites
        are not detected by ordinary pulls; finalized incident hashes are verified
        in full at sealing. Actual local length, never a cursor, sets the offset.
        """
        info = self.owned_info(relative, claim)
        if opened.size < info.st_size:
            raise ArchiveError(f"source shrank below archived prefix: {relative}")
        if (opened.size < claim["anchor_bytes"]
                or _prefix_hash(opened, claim["anchor_bytes"]) != claim["anchor_sha256"]):
            raise ArchiveError(f"source identity changed while copying: {relative}")
        descriptor = os.open(self.path(relative), os.O_RDWR | os.O_NOFOLLOW)
        wrote = False
        try:
            with os.fdopen(descriptor, "r+b", buffering=0) as local:
                before = os.fstat(local.fileno())
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    raise ArchiveError(f"archive identity changed: {relative}")
                if before.st_size != info.st_size:
                    raise ArchiveError(f"archive length changed during resume: {relative}")
                remaining = min(info.st_size, 64 * 1024)
                offset = info.st_size - remaining
                local.seek(offset)
                opened.stream.seek(offset)
                while remaining:
                    chunk = opened.stream.read(min(_CHUNK, remaining))
                    if not chunk or local.read(len(chunk)) != chunk:
                        raise ArchiveError(f"source resume-tail mismatch: {relative}")
                    remaining -= len(chunk)
                remaining = opened.size - info.st_size
                try:
                    while remaining:
                        self.space(min(_CHUNK, remaining))
                        chunk = opened.stream.read(min(_CHUNK, remaining))
                        if not chunk:
                            raise ArchiveError(f"source ended before advertised size: {relative}")
                        view = memoryview(chunk)
                        while view:
                            written = local.write(view)
                            if not written:
                                raise ArchiveError(f"short archive write: {relative}")
                            self.bytes_copied += written
                            self.changed.add(relative)
                            wrote = True
                            view = view[written:]
                        remaining -= len(chunk)
                finally:
                    if wrote:
                        os.utime(local.fileno(), (self.now.timestamp(), self.now.timestamp()))
                        os.fsync(local.fileno())
                    actual = os.fstat(local.fileno()).st_size
                    metadata_changed = wrote or actual != claim.get("bytes", 0)
                    if wrote or actual > claim.get("bytes", 0):
                        claim["last_ingested_at"] = _iso(self.now)
                    claim["bytes"] = actual
                    if metadata_changed:
                        self.save()
        except OSError as error:
            raise ArchiveError(f"copy failed for {relative}: {error}") from error
        return opened.size

    def segment(self, source: Source, source_relative: str, stream: str) -> None:
        with source.open(source_relative) as opened:
            identity = _first_record(opened)
            if not opened.size:
                if any(claim.get("source_name") == source_relative and claim.get("bytes", 0)
                       for claim in self.owner["segments"].values()):
                    raise ArchiveError(f"source shrank to empty: {source_relative}")
                return
            segments = self.owner["segments"]
            match = None
            for relative, claim in segments.items():
                if claim.get("stream") != stream:
                    continue
                first = claim.get("first_record_sha256")
                if first and first == identity["first_record_sha256"]:
                    match = relative
                    break
                if not first and claim["anchor_bytes"] <= opened.size:
                    if _prefix_hash(opened, claim["anchor_bytes"]) == claim["anchor_sha256"]:
                        match = relative
                        break
            if match is None:
                for claim in segments.values():
                    if (claim.get("source_name") == source_relative
                            and (not claim.get("first_record_sha256") or not identity["first_record_sha256"])):
                        raise ArchiveError(f"source partial prefix shrank or changed: {source_relative}")
                first_seen = identity["interval"] or self.now
                stamp = first_seen.strftime("%Y%m%dT%H%M%S%fZ")
                suffix = identity["first_record_sha256"] or ("partial-" + identity["anchor_sha256"])
                match = f"{stream}/{stamp}-{suffix}.jsonl"
                claim = {key: value for key, value in identity.items() if key != "interval"}
                claim.update(stream=stream, source_name=source_relative, first_seen_at=_iso(first_seen),
                             last_ingested_at=_iso(self.now))
                segments[match] = claim
                self.create(match, claim)
            claim = segments[match]
            metadata_changed = claim.get("source_name") != source_relative
            self.seen_segments.add(match)
            if claim.get("pruned"):
                if opened.size < claim["bytes"]:
                    raise ArchiveError(f"source shrank after retention: {source_relative}")
                if opened.size == claim["bytes"]:
                    claim["source_name"] = source_relative
                    if metadata_changed:
                        self.save()
                    return
                claim.pop("pruned")
                self.create(match, claim)
            self.append(opened, match, claim)
            claim["source_name"] = source_relative
            if not claim.get("first_record_sha256") and identity["first_record_sha256"]:
                stamp = (identity["interval"] or _date(claim["first_seen_at"])).strftime("%Y%m%dT%H%M%S%fZ")
                promoted = f"{stream}/{stamp}-{identity['first_record_sha256']}.jsonl"
                if self.path(promoted).exists():
                    raise ArchiveError(f"archive promotion collision: {promoted}")
                os.rename(self.path(match), self.path(promoted))
                self.sync_dir(self.root / stream)
                segments[promoted] = segments.pop(match)
                self.seen_segments.discard(match)
                self.seen_segments.add(promoted)
                claim.update({key: value for key, value in identity.items() if key != "interval"})
                metadata_changed = True
            if metadata_changed:
                self.save()

    def receipt(self, name: str, claim: dict[str, Any], *, error: str | None = None) -> None:
        if claim.get("sealed"):
            return
        claim["source_available"] = error != "source_missing"
        document = {"schema": "eg4_archive_incident.v1", "entry_id": self.entry_id,
                    "incident_id": claim.get("incident_id"), "state": "moving",
                    "source_state": claim.get("source_state"), "source_complete": claim.get("source_complete", False),
                    "received_complete": False, "source_available": error != "source_missing",
                    "received_at": _iso(self.now), "manifest_sha256": claim.get("manifest_sha256"),
                    "files": claim["files"], "error": error[:4096] if error else None}
        self.json(f"incidents/{name}/archive-receipt.json", document)

    def incident(self, source: Source, name: str) -> None:
        incidents = self.owner["incidents"]
        if name in incidents and incidents[name].get("sealed"):
            return
        root = f"incidents/{name}"
        manifest, payload = _source_metadata(source, f"{root}/manifest.json")
        if (manifest.get("schema") != "eg4_host_incident.v1" or manifest.get("entry_id") != self.entry_id
                or name != f"incident-{manifest.get('incident_id')}"):
            raise ArchiveError(f"incident source identity mismatch: {name}")
        if manifest.get("state") not in ("open", "closed", "interrupted"):
            raise ArchiveError(f"invalid source incident state: {name}")
        claim = incidents.get(name)
        if claim is None:
            claim = {"incident_id": manifest["incident_id"], "session_id": manifest.get("session_id"),
                     "sealed": False, "files": {}}
            incidents[name] = claim
            self.mkdir(root)
            self.save()
        if claim.get("session_id") != manifest.get("session_id"):
            raise ArchiveError(f"incident session identity changed: {name}")
        claim.update(source_state=manifest["state"], source_complete=manifest.get("complete") is True,
                     manifest_sha256=hashlib.sha256(payload).hexdigest())
        self.receipt(name, claim, error="transfer_in_progress")
        try:
            listed = manifest.get("files")
            if not isinstance(listed, list):
                raise ArchiveError(f"invalid source incident file list: {name}")
            references = {}
            for item in listed:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ArchiveError(f"invalid incident file reference: {name}")
                parts = _parts(item["path"])
                if len(parts) != 2 or parts[0] not in ("records", "triggers") or not parts[1].endswith(".jsonl"):
                    raise ArchiveError(f"unsafe incident file reference: {item['path']}")
                if item["path"] in references:
                    raise ArchiveError("duplicate incident file reference")
                references[item["path"]] = item
            for reference in manifest.get("trigger_files", []):
                if not isinstance(reference, str) or reference not in references or _parts(reference)[0] != "triggers":
                    raise ArchiveError("unsafe or unlisted incident trigger reference")
            children = _entries(source, root)
            inventory = {}
            for kind in ("records", "triggers"):
                if kind not in children:
                    continue
                if not children[kind].is_dir:
                    raise ArchiveError(f"incident evidence directory is not a directory: {kind}")
                self.mkdir(f"{root}/{kind}")
                for filename, entry in _entries(source, f"{root}/{kind}").items():
                    if not entry.is_file or not filename.endswith(".jsonl"):
                        continue
                    relative = f"{kind}/{filename}"
                    with source.open(f"{root}/{relative}") as opened:
                        inventory[relative] = _first_record(opened)
            files = claim["files"]
            # A rotated source prefix moves to its native final basename first.
            for relative, identity in sorted(inventory.items(), key=lambda item: item[0].endswith("/local-dongle.jsonl")):
                if relative in files:
                    existing = files[relative]
                    same = (existing.get("first_record_sha256") == identity["first_record_sha256"]
                            and identity["first_record_sha256"] is not None)
                    if same or not existing.get("first_record_sha256"):
                        continue
                matching = next((old for old, previous in files.items()
                                 if previous.get("first_record_sha256")
                                 and previous["first_record_sha256"] == identity["first_record_sha256"]), None)
                if relative in files:
                    old_claim = files.pop(relative)
                    self.owned_info(f"{root}/{relative}", old_claim)
                    preserved = f"{relative.split('/')[0]}/received-{uuid4().hex}.jsonl"
                    os.rename(self.path(f"{root}/{relative}"), self.path(f"{root}/{preserved}"))
                    files[preserved] = old_claim
                    self.sync_dir((self.root / root / relative).parent)
                if matching is not None:
                    old_claim = files.pop(matching)
                    self.owned_info(f"{root}/{matching}", old_claim)
                    os.rename(self.path(f"{root}/{matching}"), self.path(f"{root}/{relative}"))
                    files[relative] = old_claim
                    self.sync_dir((self.root / root / relative).parent)
                self.save()
            received = {}
            for relative, identity in inventory.items():
                full = f"{root}/{relative}"
                if relative not in files:
                    files[relative] = {key: value for key, value in identity.items() if key != "interval"}
                    self.create(full, files[relative])
                file_claim = files[relative]
                with source.open(full) as opened:
                    size = self.append(opened, full, file_claim)
                file_claim.update({key: value for key, value in identity.items() if key != "interval"})
                file_claim.pop("sha256", None)
                received[relative] = size
            self.atomic(f"{root}/manifest.json", payload)
            self.save()
            if manifest["state"] == "open":
                self.receipt(name, claim)
                return
            local_hashes = {}
            for relative, file_claim in files.items():
                size, digest = self.file_hash(f"{root}/{relative}", file_claim)
                local_hashes[relative] = (size, digest)
                file_claim.update(bytes=size, sha256=digest, source_available=relative in received)
            for relative, metadata in references.items():
                retained = metadata.get("retained")
                if retained is not True and retained is not False:
                    raise ArchiveError(f"invalid incident retention flag: {relative}")
                expected = metadata.get("sha256")
                if relative in local_hashes and (relative in received or expected is not None):
                    # A final source hash can authenticate a received file even
                    # after HA pruning; an open checkpoint cannot.
                    size, digest = local_hashes[relative]
                    if relative in received and received[relative] != size:
                        raise ArchiveError(f"received incident size changed: {relative}")
                elif retained is False:
                    continue
                else:
                    raise ArchiveError(f"retained incident file missing: {relative}")
                if type(metadata.get("bytes")) is not int or metadata["bytes"] != size:
                    raise ArchiveError(f"final incident size mismatch: {relative}")
                if retained and manifest["state"] == "closed" and metadata.get("finalized") is not True:
                    raise ArchiveError(f"closed incident file is not finalized: {relative}")
                if expected is not None and (not isinstance(expected, str) or expected.lower() != digest):
                    raise ArchiveError(f"final incident hash mismatch: {relative}")
                files[relative]["source_final_hash_verified"] = expected is not None
            if any(relative not in references for relative in received):
                raise ArchiveError("final incident manifest omits available evidence")
            # Verify stable file bytes again, then bracket them with the same manifest.
            for relative, size in received.items():
                digest = local_hashes[relative][1]
                with source.open(f"{root}/{relative}") as opened:
                    if opened.size != size or _prefix_hash(opened, size) != digest:
                        raise ArchiveError(f"incident changed while sealing: {relative}")
                if self.file_hash(f"{root}/{relative}", files[relative]) != (size, digest):
                    raise ArchiveError(f"received incident hash mismatch: {relative}")
            _, after = _source_metadata(source, f"{root}/manifest.json")
            if after != payload:
                raise ArchiveError("incident manifest changed while sealing")
            _, local_manifest = _source_metadata(LocalSource(self.root), f"{root}/manifest.json")
            if local_manifest != payload:
                raise ArchiveError("received incident manifest hash mismatch")
            receipt = {"schema": "eg4_archive_incident.v1", "entry_id": self.entry_id,
                       "incident_id": claim["incident_id"], "state": "sealed", "custody_verified": True,
                       "source_state": manifest["state"], "source_complete": manifest.get("complete") is True,
                       "received_complete": (manifest.get("complete") is True and bool(references)
                                             and all(item.get("retained") is True for item in references.values())),
                       "source_available": True, "received_at": _iso(self.now),
                       "manifest_sha256": claim["manifest_sha256"], "files": files, "error": None}
            self.json(f"{root}/archive-receipt.json", receipt)
            claim["sealed"] = True
            # The permanent receipt owns finalized custody detail; the live
            # append index need not rewrite every sealed file forever.
            incidents[name] = {"incident_id": claim["incident_id"], "sealed": True}
            self.save()
        except Exception as error:
            self.receipt(name, claim, error=str(error))
            raise

    def prune(self, days: int) -> None:
        cutoff = self.now - timedelta(days=days)
        for relative, claim in list(self.owner["segments"].items()):
            if claim.get("pruned"):
                if relative not in self.seen_segments:
                    del self.owner["segments"][relative]
                    self.save()
                continue
            info = self.owned_info(relative, claim)
            last = _date(claim["last_ingested_at"])
            if info.st_size > claim.get("bytes", 0):
                claim.update(bytes=info.st_size, last_ingested_at=_iso(self.now))
                self.save()
                continue
            if last >= cutoff:
                continue
            # An inode replacement, even under an owned basename, is never ours to delete.
            self.owned_info(relative, claim)
            self.path(relative).unlink()
            self.sync_dir((self.root / relative).parent)
            claim["pruned"] = True
            if relative not in self.seen_segments:
                del self.owner["segments"][relative]
            self.pruned_files += 1
            self.save()

    def finish(self, error: str | None) -> dict[str, Any]:
        counts = {stream: {"files": 0, "bytes": 0} for stream in _STREAMS}
        metric_errors = []
        for relative, claim in self.owner["segments"].items():
            if claim.get("pruned"):
                continue
            try:
                info = self.owned_info(relative, claim)
                counts[claim["stream"]]["files"] += 1
                counts[claim["stream"]]["bytes"] += info.st_size
            except (OSError, ArchiveError) as caught:
                metric_errors.append(str(caught))
        if metric_errors:
            error = error or metric_errors[0]
        finished = self.now if self.controlled_clock else datetime.now(UTC)
        status = {"schema": "eg4_archive_status.v1", "state": "degraded" if error else "healthy",
                  "entry_id": self.entry_id, "started_at": _iso(self.now),
                  "finished_at": _iso(finished), "last_success_at": self.previous.get("last_success_at") if error else _iso(finished),
                  "error": error[:4096] if error else None, "raw_files": counts["raw"]["files"], "raw_bytes": counts["raw"]["bytes"],
                  "journal_files": counts["controller-observations"]["files"],
                  "journal_bytes": counts["controller-observations"]["bytes"],
                  "incidents_total": len(self.owner["incidents"]),
                  "sealed_incidents": sum(bool(item.get("sealed")) for item in self.owner["incidents"].values()),
                  "unavailable_incidents": sum(item.get("source_available") is False
                                               for item in self.owner["incidents"].values()),
                  "bytes_copied": self.bytes_copied, "files_changed": len(self.changed), "pruned_files": self.pruned_files}
        self.json("status.json", status)
        return status


def archive_once(source: Source, destination: Path, *, entry_id: str, raw_days: int = 8,
                 min_free_bytes: int = 10 * 1024**3, now: datetime | None = None) -> dict[str, Any]:
    """Backfill/resume the source, preserving failure prefixes and publishing custody."""
    try:
        with _Archive(destination, entry_id, _time(now), controlled_clock=now is not None) as archive:
            try:
                if type(raw_days) is not int or raw_days < 7:
                    raise ArchiveError("raw_days must be an integer of at least seven")
                if type(min_free_bytes) is not int or min_free_bytes < 0:
                    raise ArchiveError("min_free_bytes must be a nonnegative integer")
                archive.min_free = min_free_bytes
                marker, payload = _source_metadata(source, "recorder.json")
                if marker.get("schema") != "eg4_host_recorder.v1" or marker.get("entry_id") != entry_id:
                    raise ArchiveError("source recorder identity mismatch")
                children = _entries(source, "")
                archive.atomic("recorder.json", payload)
                for stream in _STREAMS:
                    if stream not in children:
                        continue
                    if not children[stream].is_dir:
                        raise ArchiveError(f"source stream is not a directory: {stream}")
                    for name, entry in sorted(_entries(source, stream).items()):
                        if entry.is_file and name.endswith(".jsonl"):
                            archive.segment(source, f"{stream}/{name}", stream)
                available = set()
                if "incidents" in children:
                    if not children["incidents"].is_dir:
                        raise ArchiveError("source incidents is not a directory")
                    for name, entry in sorted(_entries(source, "incidents").items()):
                        if _INCIDENT_NAME.fullmatch(name):
                            if not entry.is_dir:
                                raise ArchiveError(f"source incident is not a directory: {name}")
                            available.add(name)
                            archive.incident(source, name)
                for name, claim in archive.owner["incidents"].items():
                    if name not in available and not claim.get("sealed"):
                        archive.receipt(name, claim, error="source_missing")
                archive.prune(raw_days)
                status = archive.finish(None)
                if status["state"] != "healthy":
                    raise ArchiveError(status["error"])
                return status
            except Exception as error:
                archive.finish(str(error))
                raise ArchiveError(str(error)) from error
    except ArchiveError:
        raise
    except Exception as error:
        raise ArchiveError(str(error)) from error


def record_failure(destination: Path, *, entry_id: str, error: str,
                   now: datetime | None = None) -> dict[str, Any]:
    """Publish connection failure without touching captured bytes or prior success."""
    try:
        with _Archive(destination, entry_id, _time(now), controlled_clock=now is not None) as archive:
            return archive.finish(error or "source connection failed")
    except ArchiveError:
        raise
    except Exception as caught:
        raise ArchiveError(str(caught)) from caught
