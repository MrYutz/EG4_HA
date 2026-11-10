"""Exercise the archival CLI against real temporary files and loopback SSH/SFTP."""
from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest

from scripts.eg4_archive import ArchiveError
from scripts.pull_eg4_archive import SFTPSource

CLI = Path(__file__).resolve().parents[1] / "scripts" / "pull_eg4_archive.py"
ENTRY_ID = "synthetic-sftp-archive"


def recorder(root: Path) -> bytes:
    (root / "raw").mkdir(parents=True)
    (root / "recorder.json").write_text(json.dumps({
        "schema": "eg4_host_recorder.v1", "entry_id": ENTRY_ID,
    }))
    original = (json.dumps({
        "schema": "eg4_local_dongle_trace.v2", "kind": "transaction",
        "session_id": "synthetic-sftp", "role": "gridboss",
        "transaction_id": "rejected-wire", "acquisition_id": "rejected-acquisition",
        "persistence_started_at": "2026-11-10T12:00:00+00:00",
        "started_at": "2026-11-10T12:00:00+00:00", "ok": False,
        "frames": [{"partial": True, "bytes_hex": "a11a0100"}],
        "error": "synthetic interrupted response",
    }) + "\n").encode()
    (root / "raw" / "local-dongle.jsonl").write_bytes(original)
    return original


def invoke(destination: Path, *route: str, no_site: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *(["-S"] if no_site else []), str(CLI),
         "--destination", str(destination), "--entry-id", ENTRY_ID,
         "--min-free-bytes", "0", *route],
        capture_output=True, text=True, check=False, timeout=45,
    )


def archived_raw(destination: Path) -> list[bytes]:
    return [path.read_bytes() for path in (destination / "raw").glob("*.jsonl")]


@dataclass
class LoopbackSFTP:
    root: Path
    port: int
    identity: Path
    known_hosts: Path
    requests: list[tuple[str, str]]

    def route(self, *, identity: Path | None = None, known_hosts: Path | None = None) -> list[str]:
        return [
            "--source", "/recorder", "--host", "127.0.0.1", "--port", str(self.port),
            "--user", "archive-test", "--identity", str(identity or self.identity),
            "--known-hosts", str(known_hosts or self.known_hosts),
        ]


@pytest.fixture
def sftp_server(tmp_path: Path) -> Iterator[LoopbackSFTP]:
    """A real SSH server accepting only a temporary key, with file-only SFTP."""
    paramiko = pytest.importorskip("paramiko")
    root = tmp_path / "server"
    root.mkdir()
    identity = tmp_path / "identity"
    client_key = paramiko.RSAKey.generate(2048)
    client_key.write_private_key_file(str(identity))
    identity.chmod(0o600)
    host_key = paramiko.RSAKey.generate(2048)
    requests: list[tuple[str, str]] = []

    class Authentication(paramiko.ServerInterface):
        def check_auth_publickey(self, username, key):
            if username == "archive-test" and key == client_key:
                return paramiko.AUTH_SUCCESSFUL
            return paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "publickey"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            requests.append(("exec", str(command)))
            return False

        def check_channel_shell_request(self, channel):
            requests.append(("shell", ""))
            return False

    class Handle(paramiko.SFTPHandle):
        def stat(self):
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    class Files(paramiko.SFTPServerInterface):
        def path(self, name):
            path = (root / name.lstrip("/")).resolve()
            if not path.is_relative_to(root):
                raise OSError(errno.EACCES, "outside temporary SFTP root")
            return path

        def list_folder(self, name):
            requests.append(("list", name))
            try:
                result = []
                for path in self.path(name).iterdir():
                    attributes = paramiko.SFTPAttributes.from_stat(path.lstat())
                    attributes.filename = path.name
                    result.append(attributes)
                return result
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def lstat(self, name):
            requests.append(("lstat", name))
            try:
                path = root / name.lstrip("/")
                if ".." in Path(name).parts or not path.parent.resolve().is_relative_to(root):
                    return paramiko.SFTP_PERMISSION_DENIED
                return paramiko.SFTPAttributes.from_stat(path.lstat())
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def open(self, name, flags, attr):
            requests.append(("open", name))
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
                requests.append(("write", name))
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                handle = Handle(flags)
                handle.readfile = self.path(name).open("rb")
                return handle
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key.get_name()} {host_key.get_base64()}\n")
    transports = []
    workers = []
    errors = []
    stop = threading.Event()

    def serve_connection(connection):
        transport = paramiko.Transport(connection)
        transports.append(transport)
        try:
            transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            transport.start_server(server=Authentication())
            while transport.is_active() and not stop.wait(0.01):
                pass
        except (EOFError, OSError, paramiko.SSHException):
            # Rejected host keys deliberately close SSH before authentication.
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            transport.close()

    def accept_connections():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(target=serve_connection, args=(connection,), daemon=True)
            workers.append(worker)
            worker.start()

    thread = threading.Thread(target=accept_connections, daemon=True)
    thread.start()
    try:
        yield LoopbackSFTP(root, port, identity, known_hosts, requests)
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=5)
        for transport in transports:
            transport.close()
        for worker in workers:
            worker.join(timeout=5)
        assert not thread.is_alive()
        assert not any(worker.is_alive() for worker in workers)
        assert not errors


def test_local_cli_is_stdlib_only_and_preserves_rejected_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    original = recorder(source)
    destination = tmp_path / "archive"
    result = invoke(destination, "--local", "--source", str(source), no_site=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "healthy"
    assert archived_raw(destination) == [original]
    assert (source / "raw" / "local-dongle.jsonl").read_bytes() == original


def test_sftp_cli_ingests_real_bytes_and_resumes_append(sftp_server: LoopbackSFTP, tmp_path: Path) -> None:
    source = sftp_server.root / "recorder"
    original = recorder(source)
    destination = tmp_path / "archive"
    first = invoke(destination, *sftp_server.route())
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["state"] == "healthy"
    assert archived_raw(destination) == [original]
    appended = b'{"kind":"transaction","transaction_id":"second-wire","ok":false}\n'
    with (source / "raw" / "local-dongle.jsonl").open("ab") as stream:
        stream.write(appended)
    second = invoke(destination, *sftp_server.route())
    assert second.returncode == 0, second.stdout + second.stderr
    assert archived_raw(destination) == [original + appended]
    assert json.loads(second.stdout)["bytes_copied"] == len(appended)
    assert (source / "raw" / "local-dongle.jsonl").read_bytes() == original + appended
    assert not any(operation in {"write", "exec", "shell"} for operation, _ in sftp_server.requests)
    assert destination.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("trust", ["unknown", "mismatch"])
def test_sftp_cli_rejects_untrusted_hosts_and_records_failure(
    sftp_server: LoopbackSFTP, tmp_path: Path, trust: str,
) -> None:
    import paramiko

    recorder(sftp_server.root / "recorder")
    untrusted = tmp_path / "untrusted_hosts"
    if trust == "unknown":
        untrusted.write_text("")
    else:
        other_key = paramiko.RSAKey.generate(2048)
        untrusted.write_text(
            f"[127.0.0.1]:{sftp_server.port} {other_key.get_name()} {other_key.get_base64()}\n",
        )
    prior_trust = untrusted.read_bytes()
    destination = tmp_path / "archive"
    result = invoke(destination, *sftp_server.route(known_hosts=untrusted))
    assert result.returncode != 0
    status = json.loads(result.stdout)
    assert status["state"] == "degraded"
    assert json.loads((destination / "status.json").read_text())["state"] == "degraded"
    assert not archived_raw(destination)
    assert untrusted.read_bytes() == prior_trust
    assert not sftp_server.requests


def test_sftp_auth_failure_preserves_prior_success(sftp_server: LoopbackSFTP, tmp_path: Path) -> None:
    import paramiko

    original = recorder(sftp_server.root / "recorder")
    destination = tmp_path / "archive"
    good = invoke(destination, *sftp_server.route())
    assert good.returncode == 0, good.stdout + good.stderr
    success = json.loads(good.stdout)["last_success_at"]
    wrong_identity = tmp_path / "wrong_identity"
    paramiko.RSAKey.generate(2048).write_private_key_file(str(wrong_identity))
    wrong_identity.chmod(0o600)
    bad = invoke(destination, *sftp_server.route(identity=wrong_identity))
    assert bad.returncode != 0
    status = json.loads(bad.stdout)
    assert status["state"] == "degraded"
    assert status["last_success_at"] == success
    assert archived_raw(destination) == [original]


def test_sftp_open_uses_server_fd_size_snapshot(sftp_server: LoopbackSFTP) -> None:
    import paramiko

    original = recorder(sftp_server.root / "recorder")
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(sftp_server.known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            "127.0.0.1", port=sftp_server.port, username="archive-test",
            key_filename=str(sftp_server.identity), allow_agent=False, look_for_keys=False,
            timeout=10, banner_timeout=10, auth_timeout=10, channel_timeout=10,
        )
        with client.open_sftp() as sftp:
            source = SFTPSource(sftp, "/recorder")
            with source.open("raw/local-dongle.jsonl") as opened:
                with (sftp_server.root / "recorder/raw/local-dongle.jsonl").open("ab") as stream:
                    stream.write(b'{"later":true}\n')
                assert opened.size == len(original)
                assert opened.stream.read(opened.size) == original


def test_missing_trust_file_is_structured_setup_failure(tmp_path: Path) -> None:
    destination = tmp_path / "archive"
    result = invoke(
        destination, "--source", "/never-read", "--host", "127.0.0.1", "--port", "1",
        "--user", "archive-test", "--identity", str(tmp_path / "absent_identity"),
        "--known-hosts", str(tmp_path / "absent_known_hosts"),
    )
    assert result.returncode != 0
    status = json.loads(result.stdout)
    assert status["state"] == "degraded"
    assert json.loads((destination / "status.json").read_text())["state"] == "degraded"


@pytest.mark.parametrize("directory", [False, True])
def test_sftp_source_rejects_symlink_escape(sftp_server: LoopbackSFTP, directory: bool) -> None:
    """A trusted server must not let an evidence-relative link expose other files."""
    import paramiko

    recorder(sftp_server.root / "recorder")
    private = sftp_server.root / "not-recorder"
    private.mkdir()
    (private / "private.jsonl").write_bytes(b"not recorder evidence\n")
    if directory:
        (sftp_server.root / "recorder/linked").symlink_to(private, target_is_directory=True)
        relative = "linked/private.jsonl"
    else:
        (sftp_server.root / "recorder/raw/linked.jsonl").symlink_to(private / "private.jsonl")
        relative = "raw/linked.jsonl"
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(sftp_server.known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            "127.0.0.1", port=sftp_server.port, username="archive-test",
            key_filename=str(sftp_server.identity), allow_agent=False, look_for_keys=False,
            timeout=10, banner_timeout=10, auth_timeout=10, channel_timeout=10,
        )
        with client.open_sftp() as sftp:
            source = SFTPSource(sftp, "/recorder")
            with pytest.raises(ArchiveError), source.open(relative):
                pass
