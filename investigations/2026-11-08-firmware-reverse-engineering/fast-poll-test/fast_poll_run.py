"""Read-only fast-poll test on owned dongle connections.

Run ONLY while the Home Assistant EG4 entry is paused. Each poller holds one
persistent TCP connection to one dongle and sends function-04 (read input
registers) requests back to back. No writes of any kind.

Phase A: GridBOSS alone.  Phase B: GridBOSS and both inverters in parallel.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import statistics
import struct
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(sys.argv[1])
IDENT_DIR = Path(sys.argv[2])
OUT = Path(sys.argv[3])
PHASE_A_S = float(sys.argv[4])
PHASE_B_S = float(sys.argv[5])
GAP = 0.01
TIMEOUT = 2.5
MAX_RECONNECTS = 5

comp = PROJECT / "custom_components" / "eg4_monitor"
pkg = types.ModuleType("eg4probe")
pkg.__path__ = [str(comp)]
sys.modules["eg4probe"] = pkg
spec = importlib.util.spec_from_file_location("eg4probe.local_dongle", comp / "local_dongle.py")
ld = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ld
spec.loader.exec_module(ld)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readexactly(6)
    if head[:2] != ld.PACKET_PREFIX:
        raise RuntimeError("bad prefix")
    length = struct.unpack_from("<H", head, 4)[0]
    if length > 4096:
        raise RuntimeError("oversize frame")
    return head + await reader.readexactly(length)


async def poll(role: str, register: int, count: int, seconds: float) -> dict:
    ident = json.loads((IDENT_DIR / f"{role}.json").read_text())
    host, port = ident["host"], int(ident["port"])
    dongle, serial = ident["dongle"], ident["serial"]
    request = ld.build_read_packet(
        dongle_serial=dongle, inverter_serial=serial,
        register=register, count=count, function=ld.MODBUS_READ_INPUT,
    )
    samples, other, events = [], {}, []
    reconnects = 0
    began = time.monotonic()
    started_at = now()
    reader = writer = None
    try:
        while time.monotonic() - began < seconds:
            if writer is None:
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
                except Exception as exc:
                    events.append(("connect_failed:" + type(exc).__name__, round(time.monotonic() - began, 3)))
                    reconnects += 1
                    if reconnects > MAX_RECONNECTS:
                        break
                    await asyncio.sleep(1.0)
                    continue
            t0 = time.monotonic()
            try:
                writer.write(request)
                await writer.drain()
                got = None
                while got is None:
                    remaining = TIMEOUT - (time.monotonic() - t0)
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    raw = await asyncio.wait_for(read_frame(reader), remaining)
                    try:
                        got = ld.parse_dongle_frame(
                            raw, expected_dongle_serial=dongle,
                            expected_inverter_serial=serial,
                            expected_register=register, expected_count=count,
                            expected_function=ld.MODBUS_READ_INPUT,
                        )
                    except Exception:
                        key = f"len{len(raw)}:tcpfn{raw[7]:02x}"
                        try:
                            f2 = ld.parse_dongle_frame(raw)
                            key = f"fn{f2.modbus_function}:reg{f2.register}:n{len(f2.values)}"
                        except Exception:
                            pass
                        other[key] = other.get(key, 0) + 1
            except asyncio.TimeoutError:
                events.append(("timeout", round(time.monotonic() - began, 3)))
                continue
            except (asyncio.IncompleteReadError, ConnectionError, OSError, RuntimeError) as exc:
                events.append(("closed:" + type(exc).__name__, round(time.monotonic() - began, 3)))
                try:
                    writer.close()
                except Exception:
                    pass
                reader = writer = None
                reconnects += 1
                if reconnects > MAX_RECONNECTS:
                    break
                await asyncio.sleep(1.0)
                continue
            t1 = time.monotonic()
            samples.append({"t": round(t0 - began, 4), "latency_ms": round((t1 - t0) * 1000, 1),
                            "values": list(got.values)})
            await asyncio.sleep(GAP)
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except Exception:
                pass
    elapsed = time.monotonic() - began
    lat = [s["latency_ms"] for s in samples]
    starts = [s["t"] for s in samples]
    spacing = [round((b - a) * 1000, 1) for a, b in zip(starts, starts[1:])]
    changed = []
    if samples:
        first = samples[0]["values"]
        changed = sorted({register + i for s in samples for i, v in enumerate(s["values"]) if v != first[i]})

    def stats(xs):
        if not xs:
            return None
        xs2 = sorted(xs)
        return {"min": xs2[0], "median": statistics.median(xs2), "p90": xs2[int(len(xs2) * 0.9)],
                "p99": xs2[min(len(xs2) - 1, int(len(xs2) * 0.99))], "max": xs2[-1]}

    return {
        "role": role, "register": register, "count": count,
        "started_at": started_at, "finished_at": now(),
        "duration_s": round(elapsed, 2), "ok": len(samples),
        "rate_hz": round(len(samples) / elapsed, 2) if elapsed else None,
        "latency_ms": stats(lat), "sample_spacing_ms": stats(spacing),
        "spacing_over_1s": sum(1 for g in spacing if g > 1000),
        "events": events, "reconnects": reconnects, "other_frames_seen": other,
        "registers_that_changed": changed,
        "first_values": samples[0]["values"] if samples else None,
        "samples": samples,
    }


async def main() -> None:
    result = {"schema": "eg4_fast_poll_test.v1", "writes_sent": 0, "phases": []}
    a = await poll("gridboss", 568, 21, PHASE_A_S)
    result["phases"].append({"name": "A: GridBOSS alone", "pollers": [a]})
    if a["ok"] == 0:
        result["aborted"] = "GridBOSS returned no data in phase A; phase B skipped"
    else:
        b = await asyncio.gather(
            poll("gridboss", 568, 21, PHASE_B_S),
            poll("flexboss_master", 0, 40, PHASE_B_S),
            poll("flexboss_slave", 0, 40, PHASE_B_S),
        )
        result["phases"].append({"name": "B: all three in parallel", "pollers": list(b)})
    OUT.write_text(json.dumps(result, indent=1))
    for ph in result["phases"]:
        print("==", ph["name"])
        for p in ph["pollers"]:
            brief = {k: p[k] for k in ("role", "register", "count", "duration_s", "ok", "rate_hz",
                                       "latency_ms", "sample_spacing_ms", "spacing_over_1s",
                                       "events", "reconnects", "other_frames_seen",
                                       "registers_that_changed")}
            print(json.dumps(brief))
    if "aborted" in result:
        print("ABORTED:", result["aborted"])


asyncio.run(main())
