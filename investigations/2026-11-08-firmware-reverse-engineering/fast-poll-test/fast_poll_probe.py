"""Read-only fast-poll probe for one EG4 dongle.

Opens ONE TCP connection, repeatedly sends a function-04 (read input
registers) request for one small block, and measures the round trip.
No writes of any kind: the project's packet builder only supports 03/04
and this script hardcodes 04.
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
from pathlib import Path

PROJECT = Path(sys.argv[1])
IDENTITY = Path(sys.argv[2])
REGISTER = int(sys.argv[3])
COUNT = int(sys.argv[4])
DURATION = float(sys.argv[5])
OUT = Path(sys.argv[6])
GAP = 0.02
TIMEOUT = 2.5

comp = PROJECT / "custom_components" / "eg4_monitor"
pkg = types.ModuleType("eg4probe")
pkg.__path__ = [str(comp)]
sys.modules["eg4probe"] = pkg
spec = importlib.util.spec_from_file_location("eg4probe.local_dongle", comp / "local_dongle.py")
ld = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ld
spec.loader.exec_module(ld)

ident = json.loads(IDENTITY.read_text())
HOST, PORT = ident["host"], int(ident["port"])
DONGLE, SERIAL = ident["dongle"], ident["serial"]


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readexactly(6)
    if head[:2] != ld.PACKET_PREFIX:
        raise RuntimeError("bad prefix")
    length = struct.unpack_from("<H", head, 4)[0]
    if length > 4096:
        raise RuntimeError("oversize frame")
    return head + await reader.readexactly(length)


async def main() -> None:
    request = ld.build_read_packet(
        dongle_serial=DONGLE, inverter_serial=SERIAL,
        register=REGISTER, count=COUNT, function=ld.MODBUS_READ_INPUT,
    )
    reader, writer = await asyncio.wait_for(asyncio.open_connection(HOST, PORT), 5)
    samples, other, errors = [], {}, []
    t_begin = time.monotonic()
    try:
        while time.monotonic() - t_begin < DURATION:
            t0 = time.monotonic()
            writer.write(request)
            await writer.drain()
            got = None
            try:
                while True:
                    remaining = TIMEOUT - (time.monotonic() - t0)
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    raw = await asyncio.wait_for(read_frame(reader), remaining)
                    try:
                        frame = ld.parse_dongle_frame(
                            raw, expected_dongle_serial=DONGLE,
                            expected_inverter_serial=SERIAL,
                            expected_register=REGISTER, expected_count=COUNT,
                            expected_function=ld.MODBUS_READ_INPUT,
                        )
                        got = frame
                        break
                    except Exception as exc:  # another client's traffic, heartbeat, etc.
                        key = f"len{len(raw)}:tcpfn{raw[7]:02x}:{type(exc).__name__}"
                        try:
                            f2 = ld.parse_dongle_frame(raw)
                            key = f"fn{f2.modbus_function}:reg{f2.register}:n{len(f2.values)}"
                        except Exception:
                            pass
                        other[key] = other.get(key, 0) + 1
            except asyncio.TimeoutError:
                errors.append(("timeout", round(time.monotonic() - t_begin, 3)))
                continue
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
                errors.append(("closed_by_peer:" + type(exc).__name__, round(time.monotonic() - t_begin, 3)))
                break
            t1 = time.monotonic()
            samples.append({
                "t": round(t0 - t_begin, 4),
                "latency_ms": round((t1 - t0) * 1000, 1),
                "values": list(got.values),
            })
            await asyncio.sleep(GAP)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except Exception:
            pass

    elapsed = time.monotonic() - t_begin
    lat = [s["latency_ms"] for s in samples]
    starts = [s["t"] for s in samples]
    gaps = [round((b - a) * 1000, 1) for a, b in zip(starts, starts[1:])]
    summary = {
        "register": REGISTER, "count": COUNT, "duration_s": round(elapsed, 2),
        "requests": len(samples) + len(errors), "ok": len(samples),
        "errors": errors,
        "rate_hz": round(len(samples) / elapsed, 2) if elapsed else None,
        "latency_ms": {
            "min": min(lat), "median": statistics.median(lat),
            "p90": sorted(lat)[int(len(lat) * 0.9)], "max": max(lat),
        } if lat else None,
        "sample_spacing_ms": {
            "median": statistics.median(gaps), "p90": sorted(gaps)[int(len(gaps) * 0.9)],
            "max": max(gaps),
        } if gaps else None,
        "other_frames_seen": other,
    }
    OUT.write_text(json.dumps({"summary": summary, "samples": samples}, indent=1))
    print(json.dumps(summary, indent=1))
    if samples:
        first, last = samples[0]["values"], samples[-1]["values"]
        changed = sorted({REGISTER + i for s in samples for i, v in enumerate(s["values"]) if v != first[i]})
        print("first:", {REGISTER + i: v for i, v in enumerate(first)})
        print("registers that changed during the run:", changed)


asyncio.run(main())
