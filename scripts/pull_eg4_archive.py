#!/usr/bin/env python3
"""Pull existing recorder evidence, locally or through key-only, trusted SFTP."""
from __future__ import annotations

import argparse
import json
import stat
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

if __package__:
    from .eg4_archive import (
        ArchiveError,
        LocalSource,
        SourceEntry,
        SourceRead,
        archive_once,
        record_failure,
    )
else:
    from eg4_archive import (
        ArchiveError,
        LocalSource,
        SourceEntry,
        SourceRead,
        archive_once,
        record_failure,
    )

CONNECT_TIMEOUT = 10
AUTH_TIMEOUT = 10
READ_TIMEOUT = 30


class SFTPSource:
    """Read-only Source adapter; each opened server fd supplies its size snapshot."""

    def __init__(self, client: Any, root: str) -> None:
        if (not root or "\x00" in root or "\\" in root
                or not PurePosixPath(root).is_absolute() or ".." in PurePosixPath(root).parts):
            raise ArchiveError("SFTP source root must be an absolute, traversal-free path")
        self.client = client
        self.root = root.rstrip("/") or "/"
        self.client.get_channel().settimeout(READ_TIMEOUT)

    def _path(self, relative: str) -> str:
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or "\\" in relative
                or "\x00" in relative or (relative and str(path) != relative)):
            raise ArchiveError("invalid source-relative path")
        parts = (*PurePosixPath(self.root).parts[1:], *path.parts)
        current = PurePosixPath("/")
        for index, part in enumerate(parts):
            current /= part
            mode = self.client.lstat(str(current)).st_mode or 0
            if stat.S_ISLNK(mode):
                raise ArchiveError("SFTP source symlink is not recorder evidence")
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise ArchiveError("SFTP source ancestor is not a directory")
        return str(current)

    def listdir(self, relative: str) -> list[SourceEntry]:
        entries = []
        for item in self.client.listdir_attr(self._path(relative)):
            name = item.filename
            if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise ArchiveError("invalid SFTP directory entry")
            mode = item.st_mode or 0
            entries.append(SourceEntry(name, stat.S_ISDIR(mode), stat.S_ISREG(mode)))
        return entries

    @contextmanager
    def open(self, relative: str) -> Iterator[SourceRead]:
        with self.client.open(self._path(relative), "rb") as stream:
            attributes = stream.stat()
            if not stat.S_ISREG(attributes.st_mode or 0):
                raise ArchiveError("SFTP source is not a regular file")
            if attributes.st_size is None or attributes.st_size < 0:
                raise ArchiveError("SFTP server did not supply a valid fd size")
            yield SourceRead(stream, attributes.st_size)


@contextmanager
def connect_source(args: argparse.Namespace) -> Iterator[SFTPSource]:
    """Use existing host trust and the one supplied key, never agents/passwords."""
    import paramiko

    client = paramiko.SSHClient()
    try:
        client.load_host_keys(str(args.known_hosts.expanduser()))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=args.host, port=args.port, username=args.user,
            key_filename=str(args.identity.expanduser()),
            allow_agent=False, look_for_keys=False, password=None, passphrase=None,
            timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=AUTH_TIMEOUT, channel_timeout=READ_TIMEOUT,
        )
        transport = client.get_transport()
        if transport is None:
            raise ArchiveError("SSH connection has no transport")
        # Channel requests and SFTP version negotiation must also have a deadline:
        # setting the channel's socket timeout alone does not bound subsystem ACKs.
        deadline = threading.Timer(READ_TIMEOUT, client.close)
        deadline.daemon = True
        deadline.start()
        try:
            channel = transport.open_session(timeout=READ_TIMEOUT)
            channel.settimeout(READ_TIMEOUT)
            channel.invoke_subsystem("sftp")
            sftp = paramiko.SFTPClient(channel)
        finally:
            deadline.cancel()
        with sftp:
            yield SFTPSource(sftp, args.source)
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="exact recorder root; no remote shell expansion")
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--entry-id", required=True)
    route = parser.add_mutually_exclusive_group(required=True)
    route.add_argument("--local", action="store_true", help="use stdlib-only local file reads")
    route.add_argument("--host")
    parser.add_argument("--user")
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--known-hosts", type=Path, default=Path("~/.ssh/known_hosts"))
    parser.add_argument("--raw-days", type=int, default=8)
    parser.add_argument("--min-free-bytes", type=int, default=10 * 1024**3)
    args = parser.parse_args(argv)
    if not args.local and (not args.user or args.identity is None):
        parser.error("--host requires --user and --identity")
    if args.local and (args.user or args.identity is not None):
        parser.error("--local cannot use SSH authentication options")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.raw_days < 7 or args.min_free_bytes < 0:
        parser.error("--raw-days must be at least 7 and --min-free-bytes nonnegative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.local:
            status = archive_once(
                LocalSource(Path(args.source)), args.destination,
                entry_id=args.entry_id, raw_days=args.raw_days,
                min_free_bytes=args.min_free_bytes,
            )
        else:
            with connect_source(args) as source:
                status = archive_once(
                    source, args.destination, entry_id=args.entry_id,
                    raw_days=args.raw_days, min_free_bytes=args.min_free_bytes,
                )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            status = record_failure(args.destination, entry_id=args.entry_id, error=error)
        except Exception as recording_error:
            # Ownership/locking/disk errors may make even a status write unsafe.
            status = {
                "schema": "eg4_archive_status.v1", "state": "degraded",
                "entry_id": args.entry_id, "error": error,
                "status_recording_error": f"{type(recording_error).__name__}: {recording_error}",
            }
        print(json.dumps(status, sort_keys=True))
        return 1
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
