"""Source-bound coverage for the read-only trace CLI (stdlib, disk-backed state).

Captured profiles attest expectations; the adjacent source catalog is only a
reference for older captures. Neither a catalog nor manifest custody attests a
continuous physical observation. All ages are relative to retained evidence,
never to the wall clock running this program.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_BLOCK_WORDS = 125  # Modbus read admission, independent of the 4-MiB input bound.


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _emit(name: str, value: Any) -> None:
    print(f"{name}=" + json.dumps(value, sort_keys=True))


def _catalog():
    # Do not execute custom_components.eg4_monitor.__init__ (which requires HA).
    here = Path(__file__).resolve().parent
    candidates = [here.parent / "custom_components/eg4_monitor/register_catalog.py",
                  here / "register_catalog.py"]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("eg4_trace_register_catalog", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ValueError("required register_catalog.py unavailable; retain the repository layout or copy "
                     "the canonical register_catalog.py alongside the analyzer modules")


class Coverage:
    """Deduplicate exact source identities and calculate quantiles without RAM growth."""

    def __init__(self, *, registers: bool = False, gap_seconds: float = 5,
                 roles: list[str] | None = None):
        self.catalog = _catalog()
        self.registers = registers
        self.gap_seconds = gap_seconds
        self.roles = roles
        self.temp = tempfile.TemporaryDirectory(prefix="eg4-trace-coverage-")
        self.db = sqlite3.connect(str(Path(self.temp.name) / "coverage.sqlite"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-2048;
            CREATE TABLE groups (
                id INTEGER PRIMARY KEY, build TEXT, session TEXT, scope TEXT,
                start REAL, end REAL, UNIQUE(build,session,scope));
            CREATE TABLE roles (g INTEGER, role TEXT, expected INTEGER DEFAULT 0,
                observed INTEGER DEFAULT 0, seen INTEGER DEFAULT 0, profile TEXT, PRIMARY KEY(g,role));
            CREATE TABLE samples (
                id INTEGER PRIMARY KEY, g INTEGER, role TEXT, identity TEXT,
                fn INTEGER, reg INTEGER, count INTEGER, tx TEXT, acq TEXT,
                start REAL, end REAL, latency REAL, ok INTEGER, structural TEXT,
                semantic TEXT, source TEXT, words TEXT, copies INTEGER DEFAULT 1,
                canonical INTEGER DEFAULT 0, canonical_order INTEGER, cached INTEGER DEFAULT 0,
                conflict INTEGER DEFAULT 0, UNIQUE(g,role,identity,fn,reg,count));
            CREATE INDEX sample_group ON samples(g,role,fn,reg,count,start);
            CREATE INDEX sample_transaction ON samples(g,role,tx);
            CREATE TABLE verdicts (g INTEGER, role TEXT, tx TEXT, semantic TEXT,
                PRIMARY KEY(g,role,tx,semantic));
            CREATE TABLE facts (g INTEGER, category TEXT, payload TEXT,
                UNIQUE(g,category,payload));
            CREATE VIEW effective AS SELECT s.*,
                CASE WHEN semantic='rejected' OR EXISTS (
                    SELECT 1 FROM verdicts v WHERE v.g=s.g AND v.role=s.role
                    AND v.tx=s.tx AND v.semantic='rejected') THEN 'rejected'
                WHEN semantic='accepted' OR EXISTS (
                    SELECT 1 FROM verdicts v WHERE v.g=s.g AND v.role=s.role
                    AND v.tx=s.tx AND v.semantic='accepted') THEN 'accepted'
                ELSE 'not_evaluated' END AS verdict FROM samples s;
        """)
        self.sequence = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()
        self.temp.cleanup()

    def _group(self, row: dict, path: str) -> int:
        build = row.get("build_id")
        session = row.get("session_id")
        build = build if isinstance(build, str) and build else ""
        session = session if isinstance(session, str) and session else ""
        # No identity fabrication: an anonymous source remains file-local.
        scope = "" if session else path
        self.db.execute("INSERT OR IGNORE INTO groups(build,session,scope) VALUES(?,?,?)",
                        (build, session, scope))
        group = self.db.execute("SELECT id FROM groups WHERE build=? AND session=? AND scope=?",
                                (build, session, scope)).fetchone()[0]
        end = max((stamp for key in ("completed_at", "sample_completed_at", "observed_at",
                                     "captured_at", "closed_at")
                   if (stamp := _time(row.get(key))) is not None), default=None)
        if end is not None:
            self.db.execute("UPDATE groups SET end=MAX(COALESCE(end,?),?) WHERE id=?", (end, end, group))
        start = min((stamp for key in ("started_at", "sample_started_at", "observed_at")
                     if (stamp := _time(row.get(key))) is not None), default=None)
        if start is not None:
            self.db.execute("UPDATE groups SET start=MIN(COALESCE(start,?),?) WHERE id=?", (start, start, group))
        return group

    def _role(self, group: int, role: str, *, expected: bool = False,
              observed: bool = False, seen: bool = False, profile: dict | None = None) -> None:
        self.db.execute("""INSERT INTO roles(g,role,expected,observed,seen,profile) VALUES(?,?,?,?,?,?)
            ON CONFLICT(g,role) DO UPDATE SET expected=MAX(expected,excluded.expected),
            observed=MAX(observed,excluded.observed),seen=MAX(seen,excluded.seen),
            profile=COALESCE(excluded.profile,profile)""",
            (group, role, int(expected), int(observed), int(seen or observed),
             _json(profile) if profile is not None else None))

    def fact(self, row: dict, path: str, category: str, payload: Any) -> None:
        group = self._group(row, path)
        self.db.execute("INSERT OR IGNORE INTO facts VALUES(?,?,?)", (group, category, _json(payload)))

    def metadata(self, row: dict, path: str) -> None:
        group = self._group(row, path)
        health = _object(row.get("health"))
        expected = row.get("expected_roles", health.get("ConfiguredRoles", []))
        if isinstance(expected, list):
            for role in expected:
                if isinstance(role, str) and (not self.roles or role in self.roles):
                    self._role(group, role, expected=True)
        for role, details in _object(health.get("Roles")).items():
            if self.roles and role not in self.roles:
                continue
            profile = _object(details).get("capture_profile")
            self._role(group, role, expected=True, profile=profile if isinstance(profile, dict) else None)
        if health:
            self.fact(row, path, "recorder_health", health)
        for key in ("gaps", "truncated", "incomplete_reasons", "errors", "coverage", "missing_roles"):
            if key in row:
                self.fact(row, path, key, row[key])
        if row.get("schema") == "eg4_host_incident.v1":
            self._contexts(row.get("configuration_context"), row, path, "manifest.configuration_context")

    def observe(self, row: dict, path: str) -> None:
        self.sequence += 1
        self.metadata(row, path)
        role = row.get("role", "unknown")
        if not isinstance(role, str) or (
            self.roles and role not in self.roles and row.get("kind") != "incident_context"
        ):
            return
        group = self._group(row, path)
        if role not in ("recorder", "unknown"):
            self._role(group, role, seen=True)
        if row.get("kind") == "interpretation":
            tx, verdict = row.get("transaction_id"), row.get("semantic")
            if isinstance(tx, str) and verdict in ("accepted", "rejected"):
                self.db.execute("INSERT OR IGNORE INTO verdicts VALUES(?,?,?,?)", (group, role, tx, verdict))
            return
        if row.get("kind") == "transaction":
            request = _object(row.get("request"))
            frames = row.get("frames", [])
            matched = next((frame for frame in reversed(frames) if isinstance(frame, dict)
                            and frame.get("classification") == "matching"), {}) if isinstance(frames, list) else {}
            block = {**row, **request, "values": matched.get("values"),
                     "validation": matched.get("validation", {}),
                     "partial": row.get("partial") is True or matched.get("partial") is True}
            self._sample(block, row, path, "transaction", canonical=True)
            if self.registers and isinstance(frames, list):
                for frame in frames:
                    if isinstance(frame, dict) and frame is not matched and "values" in frame:
                        _emit("unaccepted_register_frame", {
                            "source_file": path, "build_id": row.get("build_id"),
                            "session_id": row.get("session_id"), "role": role,
                            "transaction_id": row.get("transaction_id"),
                            "requested_shape": request,
                            "frame": {
                                **frame,
                                "values": frame["values"][:MAX_BLOCK_WORDS]
                                if isinstance(frame["values"], list) else frame["values"],
                                "omitted_words": max(0, len(frame["values"]) - MAX_BLOCK_WORDS)
                                if isinstance(frame["values"], list) else 0,
                            },
                            "quality": "not_promoted_to_requested_registers"})
            return
        if row.get("kind") == "direct_baseline":
            self._sample(row, row, path, "direct_baseline", canonical=True)
            return
        profile = row.get("capture_profile")
        if isinstance(profile, dict):
            self._role(group, role, expected=True, profile=profile)
        for key in ("primary_blocks", "diagnostics", "blocks"):
            blocks = row.get(key, [])
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict):
                        self._sample(block, row, path, key)
        for key in ("configuration_context", "inventory_context", "bms_context"):
            self._contexts(row.get(key), row, path, key)
        # Legacy telemetry sometimes retained words but not per-query intervals.
        # Preserve those words without assigning the acquisition envelope to
        # either query or claiming a new current-build transaction.
        if not row.get("primary_blocks"):
            telemetry = _object(row.get("telemetry"))
            raw = _object(telemetry.get("raw"))
            legacy = [
                (0, _object(telemetry.get("core")).get("registers")),
                (0, raw.get("input_registers_start_0")),
                (120, raw.get("frequency_registers_start_120")),
            ]
            for start, words in legacy:
                if isinstance(words, list) and words:
                    self._sample({
                        "function": 4, "register": start, "count": len(words),
                        "values": words, "ok": row.get("ok"),
                        "acquisition_id": row.get("acquisition_id"),
                        "semantic": "accepted" if row.get("ok") is True else "not_evaluated",
                    }, row, path, f"legacy_telemetry:{start}")

    def _contexts(self, context: Any, parent: dict, path: str, location: str, depth: int = 0) -> None:
        if not isinstance(context, dict) or depth > 8:
            return
        if "latest_attempt" in context or "last_success" in context:
            for key in ("latest_attempt", "last_success"):
                block = context.get(key)
                if isinstance(block, dict):
                    shape = {name: context[name] for name in ("function", "register", "count") if name in context}
                    self._sample({**shape, **block}, parent, path, f"{location}.{key}", cached=key == "last_success")
            return
        for key, value in context.items():
            # Incident context may be all-role rather than a single role snapshot.
            nested = {**parent, "role": key} if key in ("master", "slave", "flexboss_master", "flexboss_slave", "gridboss") else parent
            self._contexts(value, nested, path, f"{location}.{key}", depth + 1)

    def _sample(self, block: dict, parent: dict, path: str, location: str,
                *, canonical: bool = False, cached: bool = False) -> None:
        shape = (block.get("function"), block.get("register", block.get("start")),
                 block.get("count", block.get("value_or_count")))
        words = block.get("values")
        if (not all(type(value) is int for value in shape) or shape[0] not in (3, 4)
                or not 0 <= shape[1] <= 65535 or not 0 < shape[2] <= MAX_BLOCK_WORDS
                or shape[1] + shape[2] > 65536
                or isinstance(words, list) and len(words) > MAX_BLOCK_WORDS):
            self.fact(parent, path, "invalid_source", {
                "role": parent.get("role"), "location": location, "shape": shape,
                "word_count": len(words) if isinstance(words, list) else None,
                "reason": "invalid_or_oversized_read_shape_or_payload",
                "projection_admission_max_words": MAX_BLOCK_WORDS,
            })
            return
        identity = {key: block.get(key, parent.get(key)) for key in ("build_id", "session_id", "role", "register_map_revision")}
        role = identity.get("role") or "unknown"
        if not isinstance(role, str) or (self.roles and role not in self.roles):
            return
        group = self._group({
            **identity, "started_at": block.get("started_at"),
            "completed_at": block.get("completed_at", block.get("acquired_at")),
        }, path)
        self._role(group, role, observed=True)
        start_text = block.get("started_at")
        end_text = block.get("completed_at", block.get("acquired_at"))
        start, end = _time(start_text), _time(end_text)
        tx, acq = block.get("transaction_id"), block.get("acquisition_id")
        tx = tx if isinstance(tx, str) and tx else None
        acq = acq if isinstance(acq, str) and acq else None
        if tx:
            key, identity_status = _json(["transaction", tx]), "transaction_id"
        elif start is not None or end is not None:
            key, identity_status = _json(["interval", acq, start_text, end_text]), "source_interval_only"
        elif acq:
            key, identity_status = _json(["acquisition", acq]), "acquisition_shape_only_query_time_unknown"
        else:
            key, identity_status = _json(["unidentified", path, self.sequence, location]), "unidentified_copy_not_deduplicable"
        valid_words = isinstance(words, list) and len(words) == shape[2] and all(type(word) is int and 0 <= word <= 65535 for word in words)
        validation = _object(block.get("validation"))
        structural = _object(block.get("quality")).get("structural")
        invalid_validation = any(value in (False, "failed", "rejected") for value in validation.values())
        if (block.get("ok") is False or block.get("partial") is True or structural == "rejected"
                or invalid_validation or (words is not None and not valid_words)):
            structural = "rejected"
        elif block.get("ok") is True and valid_words:
            validated = all(validation.get(key) in (True, "passed") for key in (
                "framing", "crc", "identity", "function", "range",
            ))
            structural = "validated" if validated else "reported_success_unverified"
        else:
            structural = "unknown"
        ok = int(block.get("ok") is True and valid_words and structural != "rejected")
        semantic = block.get("semantic", _object(block.get("quality")).get("semantic", "not_evaluated"))
        semantic = semantic if semantic in ("accepted", "rejected") else "not_evaluated"
        if tx and semantic != "not_evaluated":
            self.db.execute("INSERT OR IGNORE INTO verdicts VALUES(?,?,?,?)", (group, role, tx, semantic))
        latency = block.get("duration_ms")
        if not _number(latency) or latency < 0:
            latency = (end - start) * 1000 if start is not None and end is not None and end >= start else None
        cached = cached or block.get("current_acquisition") is False or bool(acq and parent.get("acquisition_id") and acq != parent["acquisition_id"])
        source = {**identity, "source_file": path, "location": location, "transaction_id": tx,
                  "acquisition_id": acq, "started_at": start_text, "completed_at": end_text,
                  "identity_status": identity_status, "validation": validation,
                  "reported_age_seconds": block.get("age_seconds"),
                  "baseline_schema": block.get("baseline_schema"),
                  "baseline_frame_acceptance": [
                      frame.get("accepted") for frame in block.get("received_frames", [])
                      if isinstance(frame, dict)
                  ] if isinstance(block.get("received_frames"), list) else None}
        previous = self.db.execute("SELECT id,words,ok,canonical,structural FROM samples WHERE g=? AND role=? AND identity=? AND fn=? AND reg=? AND count=?",
                                   (group, role, key, *shape)).fetchone()
        encoded_words = _json(words) if isinstance(words, list) else None
        if previous is None:
            self.db.execute("""INSERT INTO samples(g,role,identity,fn,reg,count,tx,acq,start,end,latency,
                ok,structural,semantic,source,words,canonical,canonical_order,cached)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (group, role, key, *shape, tx, acq, start, end, latency, ok, structural, semantic,
                 _json(source), encoded_words, int(canonical), self.sequence if canonical else None, int(cached)))
        else:
            conflict = (
                previous["words"] is not None and encoded_words is not None and previous["words"] != encoded_words
                or previous["structural"] == "rejected" and bool(ok)
                or bool(previous["ok"]) and structural == "rejected"
            )
            self.db.execute("""UPDATE samples SET copies=copies+1,cached=cached+?,
                canonical_order=CASE WHEN canonical=0 AND ?=1 THEN ? ELSE canonical_order END,
                canonical=MAX(canonical,?),conflict=MAX(conflict,?),
                semantic=CASE WHEN semantic='rejected' OR ?='rejected' THEN 'rejected'
                              WHEN semantic='accepted' OR ?='accepted' THEN 'accepted'
                              ELSE 'not_evaluated' END WHERE id=?""",
                (int(cached), int(canonical), self.sequence, int(canonical),
                 int(conflict), semantic, semantic, previous["id"]))
            # A transport record is authoritative over its later cached copy.
            # Otherwise enrich missing data only; never overwrite conflicting raw words.
            if not previous["canonical"] and (
                canonical or previous["words"] is None and encoded_words is not None
            ):
                self.db.execute("UPDATE samples SET words=?,ok=?,structural=?,source=?,start=?,end=?,latency=? WHERE id=?",
                                (encoded_words, ok, structural, _json(source), start, end, latency, previous["id"]))

    def _stats(self, query: str, args: tuple = ()) -> dict:
        count, maximum = self.db.execute(f"SELECT COUNT(*),MAX(value) FROM ({query}) WHERE value IS NOT NULL", args).fetchone()
        if not count:
            return {"count": 0, "median": None, "p95": None, "max": None}
        ordered = f"SELECT value FROM ({query}) WHERE value IS NOT NULL ORDER BY value LIMIT 1 OFFSET ?"
        def at(offset):
            return self.db.execute(ordered, (*args, offset)).fetchone()[0]
        return {"count": count, "median": (at((count - 1) // 2) + at(count // 2)) / 2,
                "p95": at(math.ceil(count * .95) - 1), "max": maximum}

    @staticmethod
    def _identity(group: sqlite3.Row, role: str | None = None) -> dict:
        result = {"build_id": group["build"] or None, "session_id": group["session"] or None,
                  "anonymous_source_file": group["scope"] or None}
        if role is not None:
            result["role"] = role
        return result

    @staticmethod
    def _loss(category: str, payload: Any) -> bool:
        if category in ("input_file_gap", "manifest_custody_failure", "invalid_source"):
            return True
        if category in ("gaps", "truncated", "missing_roles", "incomplete_reasons", "errors"):
            return bool(payload)
        if category == "coverage":
            return bool(_object(payload).get("memory_truncated") or _object(payload).get("warmup"))
        if category == "recorder_health":
            for key in ("RawPersistence", "JournalPersistence", "Incidents"):
                persistence = _object(_object(payload).get(key))
                if persistence.get("State") == "degraded" or persistence.get("Enabled") is False:
                    return True
                if any(_number(value) and value > 0 for name, value in persistence.items()
                       if any(token in name.lower() for token in ("lost", "drop", "truncat", "error", "evict"))):
                    return True
        return False

    def report(self, source_group: str) -> None:
        self.db.commit()
        unlocated_loss = bool(self.db.execute(
            "SELECT EXISTS(SELECT 1 FROM facts WHERE category='input_file_gap')").fetchone()[0])
        for group in self.db.execute("SELECT * FROM groups ORDER BY id"):
            identity = {"source_group": source_group, **self._identity(group)}
            loss = unlocated_loss
            for fact in self.db.execute("SELECT category,payload FROM facts WHERE g=?", (group["id"],)):
                payload = json.loads(fact["payload"])
                affects = self._loss(fact["category"], payload)
                loss |= affects
                _emit("coverage_fact", {**identity, "category": fact["category"], "reported": payload,
                                        "limits_sample_sufficiency": affects})
            for role in self.db.execute("SELECT * FROM roles WHERE g=? ORDER BY role", (group["id"],)):
                self._report_role(group, role, identity, loss)

    def _report_role(self, group: sqlite3.Row, role: sqlite3.Row, identity: dict, loss: bool) -> None:
        role_name = role["role"]
        identity = {**identity, "role": role_name}
        regressions = self.db.execute("""SELECT COUNT(*) FROM (
            SELECT start,end,LAG(start) OVER (ORDER BY canonical_order,id) AS previous
            FROM samples WHERE g=? AND role=? AND canonical_order IS NOT NULL AND start IS NOT NULL
            ) WHERE start<previous OR end<start""", (group["id"], role_name)).fetchone()[0]
        if regressions:
            loss = True
            _emit("coverage_fact", {
                **identity, "category": "canonical_source_time_regression",
                "reported": {"count": regressions, "basis": "first_canonical_arrival_order",
                             "cause": "host_clock_regression_or_reordered_capture_unresolved"},
                "limits_sample_sufficiency": True,
            })
        profile = json.loads(role["profile"]) if role["profile"] else None
        reference = self.catalog.capture_profile(role_name) if profile is None else None
        expected = {}
        if profile is not None:
            periods = {tuple(item[:3]): item[3] for item in profile.get("effective_periods", [])
                       if isinstance(item, list) and len(item) == 4
                       and all(type(word) is int for word in item[:3]) and _number(item[3])}
            for tier in ("primary", "dynamic", "maintenance"):
                for item in profile.get(tier, []):
                    if isinstance(item, list) and len(item) >= 3 and all(type(word) is int for word in item[:3]):
                        expected[tuple(item[:3])] = {"tier": tier, "period_seconds": periods.get(tuple(item[:3]), item[3] if len(item) > 3 else None)}
        # SQL stores the union, not a growing Python sample/shape inventory.
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS shapes(fn INTEGER,reg INTEGER,count INTEGER,PRIMARY KEY(fn,reg,count))")
        self.db.execute("DELETE FROM shapes")
        self.db.executemany("INSERT OR IGNORE INTO shapes VALUES(?,?,?)", expected)
        self.db.execute("INSERT OR IGNORE INTO shapes SELECT fn,reg,count FROM samples WHERE g=? AND role=?", (group["id"], role_name))
        missing = 0
        insufficient = loss or profile is None or not role["observed"]
        block_count = 0
        for shape in self.db.execute("SELECT fn,reg,count FROM shapes ORDER BY fn,reg,count"):
            block_count += 1
            shape = tuple(shape)
            args = (group["id"], role_name, *shape)
            where = "g=? AND role=? AND fn=? AND reg=? AND count=?"
            counts = self.db.execute(f"""SELECT COUNT(*) AS samples,
                COALESCE(SUM(ok=1 AND verdict!='rejected' AND conflict=0),0) AS successes,
                COALESCE(SUM(structural='rejected'),0) AS failures,
                COALESCE(SUM(verdict='rejected'),0) AS semantic_rejections,
                COALESCE(SUM(conflict),0) AS conflicting_sources,
                COALESCE(SUM(copies-1),0) AS deduplicated_copies,
                COALESCE(SUM(canonical=0),0) AS summary_only_sources,
                COALESCE(SUM(cached),0) AS cached_copies,
                COALESCE(SUM(start IS NULL OR end IS NULL),0) AS missing_intervals,
                COALESCE(SUM(structural='unknown'),0) AS unknown_quality,
                COALESCE(SUM(verdict='not_evaluated'),0) AS semantic_unknown
                FROM effective WHERE {where}""", args).fetchone()
            counts = dict(counts)
            expectation = expected.get(shape)
            missing += int(expectation is not None and not counts["samples"])
            period = (expectation or {}).get("period_seconds")
            threshold = period if _number(period) and period > 0 else self.gap_seconds
            interval_query = f"SELECT start-LAG(start) OVER (ORDER BY start,id) AS value FROM samples WHERE {where} AND start IS NOT NULL"
            gap_query = f"SELECT start-LAG(end) OVER (ORDER BY start,id) AS value FROM samples WHERE {where} AND start IS NOT NULL AND end IS NOT NULL"
            spacing = self._stats(interval_query, args)
            gaps = self.db.execute(f"SELECT COUNT(*) FROM ({gap_query}) WHERE value>?", (*args, threshold)).fetchone()[0]
            first_start, last_end = self.db.execute(
                f"SELECT MIN(start),MAX(end) FROM samples WHERE {where}", args).fetchone()
            leading = max(0, first_start - group["start"]) if first_start is not None and group["start"] is not None else None
            trailing = max(0, group["end"] - last_end) if last_end is not None and group["end"] is not None else None
            boundary_gap = any(value is not None and value > threshold for value in (leading, trailing))
            if regressions:
                spacing = {"count": 0, "median": None, "p95": None, "max": None}
                gaps = leading = trailing = None
            order = "canonical_order DESC,id DESC" if regressions else "COALESCE(start,end) DESC,id DESC"
            latest = self.db.execute(f"SELECT * FROM effective WHERE {where} ORDER BY {order} LIMIT 1", args).fetchone()
            good = self.db.execute(f"SELECT * FROM effective WHERE {where} AND ok=1 AND verdict!='rejected' AND conflict=0 ORDER BY {order} LIMIT 1", args).fetchone()
            def source(sample):
                if sample is None:
                    return None
                age = group["end"] - sample["end"] if not regressions and group["end"] is not None and sample["end"] is not None and group["end"] >= sample["end"] else None
                return {**json.loads(sample["source"]), "structural": sample["structural"],
                        "semantic": sample["verdict"], "age_at_evidence_end_seconds": age}
            insufficient |= bool(
                not counts["successes"] or counts["failures"] or counts["semantic_rejections"]
                or counts["conflicting_sources"] or counts["missing_intervals"] or gaps or boundary_gap
            )
            _emit("block_coverage", {**identity, "function": shape[0], "register": shape[1], "count": shape[2],
                "expectation": expectation, "profile_authority": "captured_metadata" if profile is not None else "unattested_catalog_reference_only",
                **counts, "spacing_seconds": spacing,
                "latency_ms": self._stats(f"SELECT latency AS value FROM samples WHERE {where}", args),
                "interior_gaps": gaps, "gap_threshold_seconds": threshold,
                "leading_unobserved_seconds": leading, "trailing_unobserved_seconds": trailing,
                "role_canonical_time_regressions": regressions,
                "timing_order": "unresolved_clock_or_capture_order" if regressions else "host_timestamp_order_not_clock_attestation",
                "latest_attempt_ordering": ("canonical_arrival_order" if regressions else
                                            "source_time_unknown_for_untimed_samples" if counts["missing_intervals"] else "source_time"),
                "latest_attempt": source(latest), "last_good": source(good),
                "status": "missing" if not counts["samples"] else "observed_not_continuous",
                "age_reference": "retained_group_evidence_end_not_analysis_wall_clock"})
        _emit("role_coverage", {**identity, "expected_role": bool(role["expected"]),
            "observed_blocks": bool(role["observed"]), "blocks": block_count,
            "missing_expected_blocks": missing,
            "canonical_time_regressions": regressions,
            "role_observed": bool(role["seen"]),
            "status": ("missing" if not role["seen"] else
                       "observed_without_register_sources" if not role["observed"] else "observed_not_continuous"),
            "sample_sufficiency": "insufficient" if insufficient or not expected else "observed_profile_samples_only",
            "profile": profile, "catalog_reference_only": reference,
            "installed_build_match": "not_established_by_catalog", "retention_or_gap_limit": loss,
            "manifest_custody_is_sample_coverage": False})
        self._skew(group, role_name, identity, timing_unresolved=bool(regressions))
        if self.registers:
            for sample in self.db.execute("SELECT * FROM effective WHERE g=? AND role=? AND words IS NOT NULL ORDER BY id", (group["id"], role_name)):
                source = json.loads(sample["source"])
                _emit("register_projection", {**identity, "source": source,
                    "shape": [sample["fn"], sample["reg"], sample["count"]],
                    "quality": {"structural": sample["structural"], "semantic": sample["verdict"],
                                "conflicting_copies": bool(sample["conflict"]),
                                "telemetry_usable": bool(
                                    sample["ok"] and sample["structural"] == "validated"
                                    and sample["verdict"] == "accepted" and not sample["conflict"])},
                    "catalog_revision": self.catalog.REGISTER_MAP_REVISION,
                    "installed_build_match": "not_established_by_catalog",
                    "registers": self.catalog.decode_registers(role_name, sample["fn"], sample["reg"], json.loads(sample["words"]))})

    def _skew(self, group: sqlite3.Row, role: str, identity: dict, *, timing_unresolved: bool) -> None:
        if role != "gridboss":
            return  # Input 3/130 is not the Flex voltage/frequency pair.
        paired = """SELECT acq,
            SUM(reg<=3 AND reg+count>3) AS voltage_sources,
            SUM(reg<=130 AND reg+count>130) AS frequency_sources,
            MAX(CASE WHEN reg<=3 AND reg+count>3 THEN start END) AS vstart,
            MAX(CASE WHEN reg<=130 AND reg+count>130 THEN start END) AS fstart,
            SUM(verdict='rejected') AS rejected_sources
            FROM effective WHERE g=? AND role=? AND acq IS NOT NULL AND fn=4
            AND ((reg<=3 AND reg+count>3) OR (reg<=130 AND reg+count>130))
            AND ok=1 AND conflict=0 GROUP BY acq"""
        args = (group["id"], role)
        query = f"SELECT ABS(vstart-fstart) AS value FROM ({paired}) WHERE voltage_sources=1 AND frequency_sources=1"
        unpaired = self.db.execute(
            f"SELECT COUNT(*) FROM ({paired}) WHERE voltage_sources!=1 OR frequency_sources!=1 OR vstart IS NULL OR fstart IS NULL",
            args).fetchone()[0]
        anonymous = self.db.execute(
            """SELECT COUNT(*) FROM samples WHERE g=? AND role=? AND acq IS NULL AND fn=4
               AND ((reg<=3 AND reg+count>3) OR (reg<=130 AND reg+count>130))""", args).fetchone()[0]
        rejected = self.db.execute(
            f"SELECT COALESCE(SUM(rejected_sources),0) FROM ({paired})", args).fetchone()[0]
        _emit("source_query_skew", {**identity, "channels": ["input:3", "input:130"],
            "pairing": "one_source_per_channel_in_same_recorded_acquisition_only",
            "start_skew_seconds": (
                {"count": 0, "median": None, "p95": None, "max": None}
                if timing_unresolved else self._stats(query, args)),
            "semantic_rejected_sources": rejected, "quality": "structurally_accepted_raw_query_timing_only",
            "timing_order_unresolved": timing_unresolved,
            "unpaired_or_ambiguous_acquisitions": unpaired, "sources_without_acquisition_id": anonymous,
            "meaning": "host_query_separation_not_simultaneous_physical_measurement"})
