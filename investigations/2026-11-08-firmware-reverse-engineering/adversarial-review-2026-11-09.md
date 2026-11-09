# Adversarial review: healthy generator, failed power acceptance

## Scope and established premise

Review of the current investigation artifacts and capture implementation, not a branch diff. No live equipment access, settings writes, firmware operations, HA restart, or production changes were performed for this review.

**Greg explicitly confirms that the generator remained powered throughout the dropout windows, with separate multimeter evidence of approximately 240 V / 60 Hz throughout. Generator health is an established incident premise for this review.** This is separately attributed operator/instrument evidence, not a new measurement made by this review. The investigation target is why the GridBOSS/inverters stopped accepting that healthy source, entered standby or battery operation, and sometimes failed to maintain normal output.

Historical documents that leave generator instability open should not control the current investigation. Firmware-reported zero frequency is not a contradiction of the established generator evidence: the recovered firmware can deliberately zero its qualified-frequency report when internal eligibility checks fail.

## Verdict

The work establishes a credible internal control/coordination failure investigation, but **does not identify the initiating predicate or establish a repair**. The reverse engineering has identified useful stock observability and several real causal paths. The capture is materially better than the incident-day capture, but it is not yet a reliable first-cause recorder.

Three questions must remain separate:

1. **Trigger:** which controller first withdrew source eligibility, permission, or a power command?
2. **Recovery:** which latch, handshake, phase gate, or timer prevented immediate reacquisition?
3. **Output consequence:** why did an otherwise ordinary source handoff sometimes produce severe inverter-reported EPS excursions?

A recovered feedback loop may explain persistence after a trigger without explaining the trigger. A shorter warm-up may shorten recovery without fixing either the trigger or the output disturbance.

## Evidence and implementation findings

### 1. High — the recorded reasons cannot identify first cause

GridBOSS input519 is an untimestamped, last-written decision. Normal closes leave it unchanged; some openings do not update it; repeated identical decisions are indistinguishable without other evidence. The later value `0x0204` cannot diagnose the earlier incident or establish present contact position. Q1 is likewise a retained response with breadcrumbs, not a timestamped event queue.

The candidate exposes a real chain:

**GridBOSS inhibit/warm-up status → converter aggregation → returned controlword bit0 → GridBOSS reconnection veto.**

That is compatible with a healthy generator. It does not establish which side initiated the sequence or that the loop independently oscillated. The important acceptance, computed-inhibit, acknowledgement, phase, edge-latch, and age/counter inputs are not all available through ordinary reads.

An independent calculation from the saved per-event correlation found **20 of22 covered nominal low-power windows had selector2 in every retained sample within that interval**. This does not exclude a missed short selector change at onset, but it falsifies a blanket explanation requiring sustained deselection throughout each dropout. Prioritize converter permission, returned veto and acceptance/handshake state alongside false qualification. Source: [retained correlation](static-analysis/retained-raw-status-correlation.json), per-event `first_low_to_first_return` summaries.

The feedback chain also must not be oversold: converter0201 is change-triggered, a permanently high bit0 prevents reconnection rather than independently creating repeated reconnects, and GridBOSS's contribution clears when acceptance/warm-up recover. Actual message sequence, peer contributions and internal state must supply the missing changing conditions. Native anchors: [CAN evidence](decision-tree/c28x-can/native-evidence.json), `98e34..98e44` and `99467..9947f`.

**Consequence:** another complete-looking register snapshot can still show only the aftermath. Treat “519 says reason X” as a branch observation, not a root-cause verdict.

Sources: [decision tree §§2–3, 6–7](decision-tree/README.md); [qualified-frequency fork §§2–5](incident-recheck/qualified-frequency.md); [Q1 lifetime](direct-session/README.md#snapshot-retained-breadcrumbs-and-history-are-different).

### 2. High — production transport can discard a usable stream before requesting data

`local_dongle.py:890–899` assumes the first received frame provides ordinary read-response identity. If parsing fails, it immediately aborts. Its later loop tolerates unrelated frames only **after** this initial gate (`914–936`). The repository already contains a real CRC-valid unsolicited GridBOSS history record that is not an ordinary identity response.

**Exercised counterexample:** replayed that preserved 256-byte history payload inside a synthetic loopback envelope, immediately followed by a valid ordinary identity frame. The production client raised:

```text
DongleFrameError: dongle did not provide identifiable serials
```

The loopback peer received **zero request bytes**. This is a demonstrated capture defect, not evidence that history traffic caused the physical acceptance failure.

The earlier preserved pilot also had materially incomplete GridBOSS visibility: 32/169 successful primary samples before its first pause and 15/85 after its second restoration. Conversely, the direct persistent session recorded 39/39 successful GridBOSS transactions. Those different windows do not isolate persistence as the cure, but they demonstrate that “the device cannot supply data” is not an adequate explanation.

**Required improvement:** bounded first-frame demultiplexing that preserves unrelated valid traffic and continues to an identity-bearing frame; retain strict identity, range, CRC, length and deadline validation. Evaluate connection ownership/reuse separately, rather than assuming a proven hard one-client limit or adding a competing collector.

Sources: [transport](../../custom_components/eg4_monitor/local_dongle.py); [preserved unsolicited record](engineering-capture/unsolicited-gridboss-history.json); [pilot visibility](engineering-capture/README.md); [direct session](direct-session/README.md).

### 3. High — rolling retention and the sparse journal do not preserve a weekend incident

Independently streamed all eight saved files in `state-logging/live-readout/ha-trace-snapshot/`:

| Quantity | Observed value |
|---|---:|
| Records | 47,224 |
| Total bytes | 202,189,841 |
| First write | 2026-11-08 04:38:14.805 UTC |
| Last write | 2026-11-08 18:38:17.718 UTC |
| Actual saved span | 14.001 hours |

At that historical throughput, the configured 200 MiB maximum corresponds to approximately **14.52 hours** [INFERENCE], not days. This is not a measurement of the expanded current profile's retention. The 90-day session ledger is not 90 days of raw incident evidence.

The separate journal does not solve this for analog-only anomalies. Its FlexBOSS signature intentionally excludes commands142/143 and analog EPS/power values. An offline mutation of only input142 produced no observation; a subsequent limiter144 mutation did. The raw stream retains such values until rotation, but no implemented incident-freeze mechanism was found in the reviewed tracker/writer/polling path.

There is also a **discrete protection omission**, not just intentional analog filtering: input6 low bits0/1 project redirected converter fault/warning outcomes under the candidate mapping, but input6 is absent from the journal signature. Toggling either bit alone in a preserved valid record produced no journal observation. The original warning/fault locations are cleared during redirection, so watching60–63 alone is insufficient for these outcomes. Retain the verified discrete mask in the signature, not an indiscriminately noisy whole word. Native proof: [Flex instructions](static-analysis/engine/flexboss/export/instructions.tsv), lines70502–70517,70555–70575,79656–79668; [expanded mapping](direct-session/extended-signal-producers.md).

**Required improvement:** immutable event bundles with pre-event and post-event raw context, triggered by controller changes, accepted-power loss, abnormal output voltage, and observation loss—not just debounced state changes. Preserve an entire prolonged event and repeated reconnect attempts. Add measured retention/oldest-sample reporting and off-host archival; choose a retention requirement in days and size storage from actual expanded-record throughput.

Sources: [writer limits and pruning](../../custom_components/eg4_monitor/local_dongle_trace.py), lines20–21 and383–409; [signature](../../custom_components/eg4_monitor/local_dongle_trace.py), lines30–33 and206–225; [retention contract](../../docs/mother-of-all-data-loggers.md#operational-retention).

### 4. High — timing and partial-read loss can obscure which controller acted first

Independent calculation from the fixed post-upgrade 48-record projection reproduced:

| Role | Primary successes | Median start spacing | Maximum start spacing | Maximum whole acquisition |
|---|---:|---:|---:|---:|
| FlexBOSS master | 16/16 | 4.234 s | 14.999 s | 14.756 s |
| FlexBOSS slave | 16/16 | 4.234 s | 14.998 s | 14.752 s |
| GridBOSS | 16/16 | 4.235 s | 14.999 s | 14.756 s |

These are saved-window measurements, not current live-health verification. The controller journal separately contains additional availability losses/resumptions outside that fixed window.

Per-role reads are sequential; the outer loop waits for all roles. One slow device delays the next cycle for every role (`__init__.py:1976–1993`). A common sequence number does not mean simultaneous acquisition or equal firmware-cache age. GridBOSS's power block and frequency block are separate reads, but `async_read_snapshot()` only returns after both succeed (`local_dongle.py:1021–1035`); failure of the second loses the successfully acquired first block from the returned primary snapshot.

More seriously, semantic validation discards anomalous FlexBOSS core images before the caller can preserve their raw values (`local_dongle.py:630–642`). The actual incident warning at **16:36:29.641** says `invalid inverter status register: 33280`, between the last healthy AC-charge record and the abnormal-output sequence. The scalar status survives in the error log, but the complete rejected block does not survive this capture path. Do **not** reinterpret33280 as a known mode or relax validation: retain the raw frame/block and label the decoded result invalid. Source: [incident rejection log](../2026-11-07-generator-cycling/incident-1638-filtered-transport-warnings.txt).

**Required improvement:** preserve every primary block immediately, including its acquisition interval and explicit partial success. Prioritize dynamic decisions/control/status over repeated stable configuration reads; measure the resulting rate under timeout and unsolicited-traffic conditions. Keep source-acquisition intervals, host monotonic timing, process/session identity and clock uncertainty. Infer causal order only where intervals and producer freshness permit it; otherwise mark order unresolved. Faster polling alone cannot reveal unpublished internal predicates.

Sources: [fixed capture](capture-upgrade/post-restart-capture.jsonl); [deployed capture contract](capture-upgrade/README.md); [poller](../../custom_components/eg4_monitor/__init__.py); [transport](../../custom_components/eg4_monitor/local_dongle.py).

### 5. Medium — a failed journal write can permanently suppress the missing observation

`ControllerObservationTracker.observe()` commits `_previous` before persistence (`local_dongle_trace.py:221–225`). The integration queues the resulting observation with `wait=False` (`__init__.py:1720–1735`). Queue acceptance is not disk success. Later identical samples return no observation.

**Exercised counterexample:** used the actual tracker/writer with a temporary path occupied by a regular file. `async_write(wait=False)` returned a path; the worker then recorded `FileExistsError`; no journal existed. After draining the writer, the next identical sample returned `None` from the tracker.

**Consequence:** the journal can silently lack its baseline or a decisive changed state even after storage recovers, until another signature change occurs. The raw stream is an independent fallback only while available and retained.

**Required improvement:** distinguish observed state from successfully persisted state; on rejected/failed persistence mark a gap and re-emit a baseline/current observation after recovery. Expose cumulative loss and last-success evidence rather than relying on a transient `last_error` that the next successful write clears. This is a host-side fix, unrelated to changing power-control firmware.

Sources: [tracker/writer](../../custom_components/eg4_monitor/local_dongle_trace.py), lines221–225,320–347,361–381; [journal caller](../../custom_components/eg4_monitor/__init__.py), lines1720–1735.

### 6. High for firmware intervention — candidate proof is not installed-system proof

The downloaded material is release-family transfer payloads, not verified readbacks of the installed controllers, a vendor source project, or a reproducible flashable build. The correct Para075/375 hardware variant remains unresolved. Native instructions and scoped emulation substantially improve the analysis, but synthetic memory/peripheral state does not reproduce the installed electrical/control system.

The whole-ISR witness held or seeded EPS, V/F, ADC/phase and omitted complete CAN/RTOS/plant producers. Its conditional command pulses are approximately52 microseconds under the candidate clock interpretation—not the observed31-second recovery. It is not an incident reproduction or proof of a defective relay drive.

The investigation appropriately corrected multiple material mapping errors: GridBOSS4500 rather than4516, warnings60/61 rather than64/65, and engineering selectors1113/1114 rather than ordinary-settings276/277. These are now documented corrections, not current defects. They show why future write/patch decisions need independently checked native arithmetic and installed compatibility, not a plausible decompiler label.

**Required improvement:** obtain exact installed component/hardware identity and vendor confirmation of applicable mappings before controller instrumentation. Prefer an EG4-supported instrumented build or service capture. Do not patch out qualification, bypass interlocks, widen protections, or flash concatenated payloads to make cycling disappear.

Sources: [download provenance](firmware-downloads/README.md); [witness summary](decision-tree/gridboss-control/witness-summary.json); [decision-tree exercise boundary](decision-tree/README.md#5-what-was-exercised-versus-inferred); [corrected engineering selectors](engineering-capture/README.md#critical-address-corrections).

### 7. High — derived logger states can misdescribe the internal failure

**Exercised:** two valid observations of239V, qualified0Hz and0W produce `RunState=off`, `Reason=generator_voltage_absent`, `Quality=good`, and `Confidence=high` in the actual `GeneratorStateMachine`. Voltage is plainly not absent. The implementation uses low voltage **or** low frequency to classify absence, then always names voltage as the cause (`eg4_logger.py:179–202`). Given the native frequency-suppression path, this can turn internal qualification rejection into a false generator-off narrative.

**Required improvement:** separate terminal voltage presence, internally qualified-frequency status, accepted power and mechanical engine state. Emit the actual failed predicate; do not call internally suppressed frequency proof that the source stopped.

A second code-level risk affects charging chronology: failed inverter reads can reuse a prior cached sample for up to15s, yet a new outer sequence is treated as a new observation. Once one role expires, another role's zero charge value is accepted as a complete available total (`__init__.py:1564–1592,1606–1625,1938–1952`). **[INFERENCE from the production path; not exercised end-to-end here]** stale repeated values can satisfy debounce, and a slave-only zero can end a session while master charge is unknown. Preserve distinct per-role acquisition identity and completeness; a partial total must not silently become a complete zero.

Sources: [generator classifier](../../custom_components/eg4_monitor/eg4_logger.py); [aggregation/caching](../../custom_components/eg4_monitor/__init__.py); [qualified-frequency semantics](incident-recheck/qualified-frequency.md).

### 8. High for reconstruction — the catalogue and some surviving narratives are incomplete

The event catalogue follows generator low-power predicates, not every output anomaly:

- **02:35:57.907 local:** preserved master raw input20=`1593` (159.3V EPS), status64 and AC input0, outside all35 catalogue intervals. A preceding AC-loss observation exists. This is not merely an unobserved communications gap.
- **11:43:38.204 local:** the decoded bridge CSV contains EPS37.0V, status0/standby and AC input0. Adjacent GridBOSS UPS reports remain239.6/240.0V. This is a single uncorroborated decoded anomaly, without the raw words in that bridge—not proof of37V at the house.

Sources: [baseline CSV](../2026-11-07-generator-cycling/telemetry-sanitized.csv), lines15923–15932; [bridge CSV](../2026-11-07-generator-cycling/operator-test-followup-1258.csv), lines2358–2362. The recheck's global EPS-extrema neighborhoods highlight16:36 but do not enumerate every abnormal episode.

The final watch's “8 ordinary+2 major” is contradicted by the preserved [factual correction](../2026-11-07-generator-cycling/deliverables/narrative-2026-11-07-cutoff1802-v1/verification/FACTUAL-HANDOFF-1802.md): **7 ordinary requalifying intervals +2 major disturbances +1 separate long unloading/source-departure sequence**. The combined672.6s is not proven house-outage duration. The17:37 first-observed-AC-loss→return interval is172.229s;176.936s starts at the last healthy sample and is an upper observation bracket. Neither proves continuous house outage.

Additional firmware-note corrections from the independent native audit:

- `decision-tree/README.md:92` still calls the mismatch a “power-difference” predicate; it compares **selected-source voltage against EPS voltage**. Its specific CAN contribution is suppressed when mismatch asserts, not directly turned into a generator veto. Other converter fault exits can still consume that bank; this is not a harmlessness claim.
- `state-logging/firmware/REPORT.md:149` maps channel1 reasons7/8 to `b15d` bits6/7; native literal `08028ca0=b15c` plus load offset4 identifies **b160** instead. Evidence: [relay instructions](decision-tree/gridboss-control/relay-core-instructions.tsv), lines686–707, and [literal table](decision-tree/gridboss-control/query1/literals.json).
- Its line153 “immediate qualifier” interpretation of `b150/b151` is superseded by later bounded-normal zero/update-alias analysis, not an established ordinary qualification producer. See [producer closure](decision-tree/gridboss-inputs/REPORT.md), lines151–200.

Preserve original evidence; reconcile these statements in the authoritative current narrative/map rather than silently rewriting historical artifacts. A support packet must separate raw-backed anomalies, decoded-only observations, operator-correlated major incidents and sampling gaps.

## What can actually be reconstructed

All local times below are November7, UTC−04:00. They are host observation times/brackets, not exact controller transition times.

| Episode | Defensible conclusion | Remaining internal question |
|---|---|---|
| Repeated ordinary dropouts | Healthy generator remained available; accepted power disappeared and master moved away from AC charging. Typical recovery changed from roughly123s to31s following the settings intervention. | Which permission/qualification/control edge initiated rejection? |
| 13:36 and14:36 qualified-zero handoffs | GridBOSS returned130=0/source126=1; master changed32→64 while reported EPS remained approximately normal. | Which internal qualifier or coordination state rejected a healthy source? |
| 16:36 disturbance | Last master AC-charge sample16:36:28.306; failed read; battery/EPS at16:36:33.206; EPS reports161.5,95.9,282.9,185.6V. | Why was this handoff unstable when the earlier two were not? |
| 17:37 disturbance | Master reported48.9V EPS at17:37:02.871, recovered to238.0V by17:37:08.524, then collection lost much of the sequence; GridBOSS valid gap187.689s. | Same downstream failure class, but no demonstrated common initiating mechanism. |

The late16:37:10.684 first low-power entry does not date the16:36 physical/control onset. Restricting reconstruction to nominal power-drop intervals misses preceding events. The historical recheck has28,380 master records,27,984 valid,396 invalid/unavailable; maximum internal completion gap7152.022s **within its selected two-source corpus**. Much of that gap bridges10:59–12:58, for which decoded follow-up telemetry exists elsewhere; it is not proof the entire installation was unobserved for two hours. Raw-core coverage is22 of32 selected interruption intervals, with no slave stream in those sources and no coverage of17:37. Important historical519,60/61,63,142–145 and Q1 data are absent. Later measurements cannot recover those missing transitions.

The independent review recomputed those master counts, status counts and95.9/282.9V reported EPS extrema from the preserved projections. Actual delivered-output waveform, contact position and the reported equipment-damage mechanism remain separate questions; none is needed to reopen the established generator-health premise.

Sources: [incident recheck](incident-recheck/README.md); [17:37 telemetry](../2026-11-07-generator-cycling/incident-1737/capture-attempt-2026-11-07-1802-v2/TELEMETRY-VERIFICATION-1737.md); [corrected late handoff](../2026-11-07-generator-cycling/deliverables/narrative-2026-11-07-cutoff1802-v1/verification/FACTUAL-HANDOFF-1802.md). The [final watch](../2026-11-07-generator-cycling/watch-checkpoints/FINAL-COMPARISON.md) supplies later observations but its aggregate taxonomy must be read with that correction.

## Competing internal explanations and discriminating evidence

These are investigative priorities, not estimated probabilities or diagnosed causes.

| Candidate mechanism | Falsifiable prediction | Evidence needed |
|---|---|---|
| Internal false qualification / capture-age failure |569 or packed579 qualification/age state changes as130 is zeroed and source2 is rejected despite healthy generator output. | GridBOSS569,579,126/572,130,588,518/519; raw capture-age/ADC/qualification counters if stock projections cannot establish the first edge. |
| Returned inhibit and failed reacquisition handshake | GridBOSS status propagates into converter controlbit0; returned veto persists or handshake/phase guards block close after the original trigger clears. | Time-aligned0204/0214 and0201/0221,577,578/579,588,command counters; internal acceptance/acknowledgement/edge/phase state where necessary. |
| Stale/incorrect peer or source aggregation | Control or member state is stale/inconsistent while heartbeat remains fresh, or one inverter changes mode before the aggregate veto. | Both inverter streams, full peer/control frames, per-message receive ages and accepted generations; heartbeat alone is insufficient. |
| Supervisory settings/permission arbitration | Charge/discharge request or winning limit changes before GridBOSS rejection, due to schedule, battery permission, role override or synchronized settings—not generator V/F. | Both roles'0, redirected input6 bits,60–63,77,142–145, BMS provenance; timestamped settings baselines and changes; Q1 only as bounded supplementary context. |
| Separate output-transfer regulation problem | Similar acceptance/handoff transitions produce normal output in some episodes and severe output disturbance in others, distinguished by converter feedback/phase/command state. | Synchronized affected-output measurements and per-inverter command/protection state around the handoff, not only generator watts or UI standby. |

Important exclusions: a controlbit number on one processor is not the same signal on another; ordinary input0 is not C28x107e2; input128 is control-derived timing, not independent generator frequency; source1 can mean fallback, not utility restoration; clear outward faults do not prove clear internal guards.

## Capture and prevention plan

### Host changes first

1. **Preserve evidence before rotation.** Archive current raw/controller streams and timestamped settings; implement anomaly/availability-triggered frozen pre/post bundles and retention health reporting. Preserve source bytes, hashes, parser/mapping revision, device identity and explicit gaps.
2. **Fix capture availability and interpretation.** Handle unsolicited first-frame traffic; retain rejected raw images and successful partial primary blocks; make journal persistence/recovery observable and loss-aware. Add redirected input6 protection triggers, correct false voltage-absence labels and preserve partial/cached-source quality. Keep one acquisition owner. A persistent socket is a candidate to evaluate, not an already proven repair.
3. **Prioritize causal signals.** Keep both inverter roles and GridBOSS decision/control/qualifier fields at the highest measured sustainable cadence. Move stable configuration to a lower-rate change-aware stream without losing its provenance. Do not add continuous Q1 writes merely to obtain a nominal1Hz label.
4. **Audit actual settings on all three devices.** Compare GridBOSS and both inverter roles, shared-battery flags/count handling, GEN Boost, charge/discharge permissions and schedules, SOC-versus-voltage control, thresholds, warm-up versus acknowledgement delay, and synchronized parameter changes. Readbacks need effective-time/provenance; present values are not incident values. The same register number can have different meanings on different devices.

### Close the first still-unobserved control boundary

5. Seek the vendor-supported engineering trace or approved receive-only parallel-bus capture that distinguishes the first withdrawn permission from its echo. Preserve full IDs, DLCs, payloads and timestamps for relevant0201/0221/0222,0204/0214,0514,0511 and peer0202/0232 traffic. In particular0514 carries the selected-source voltage reference used by the mismatch comparison. Validate physical interface, node identities and timestamps; bus receipt still does not prove a receiver applied that generation.
6. For the separate output disturbance, use qualified synchronized measurement of the affected GridBOSS backup output and relevant inverter signals. This investigates transfer/output behavior, **not whether the generator was healthy**. Do not use a bench instrument on mains or attach to an assumed RJ45 pinout.
7. If necessary predicates remain inaccessible, request targeted controller instrumentation: a nonblocking RAM event ring at actual state/command decisions, with monotonic timestamp, sequence/generation, complete guard vector, before/after state, winning limiter, peer freshness, first-cause capture and overflow reporting. No blocking prints or flash writes in the fast control ISR. Validate timing and recovery on a bench/spare or vendor-approved setup before deployment.

### What would count as prevention evidence

Changing current and warm-up together did not isolate a cause. Existing records show interruptions continued after250A/30s, including severe episodes; generator-absent periods are zero exposure, not a successful trial. Do not present shorter recovery as fixed cycling.

A settings correction is justified when an observed, applicable setting/permission conflict predicts the recorded controller transition and a controlled comparison removes that conflict under comparable operating exposure. A firmware fix needs the analogous identified predicate/state defect, installed applicability, safe recovery and validation. Neither should disable a protection merely because it is the final gate that rejected power.

For the next recurrence, success means: the earliest observable control boundary and its uncertainty are identified; all three device streams and configuration context survive; observation loss is explicit; output consequence is distinguished from trigger/recovery; any remaining missing internal predicate is named precisely enough for a vendor trace request. “More registers,” “no alarms,” and “no cycling while the generator is off” are not acceptance criteria.

## Checks performed for this review

- Offline streaming analysis of all eight saved rolling files:47,224 records,202,189,841bytes,14.001h actual span.
- Offline analysis of48 corrected-profile records: cadence/acquisition table above;48/48 primary successes, one GPIO diagnostic timeout.
- Independent projection calculations:28,380 master rows,27,984 valid;95.9–282.9V reported EPS extrema;7152.022s maximum adjacent completion gap.
- Actual production tracker/writer fault injection using a temporary filesystem path: failed disk write followed by suppressed identical observation.
- Actual production tracker replay:142-only change omitted;144 change retained, matching the intentional signature contract.
- Actual generator state-machine replay:239V/qualified0Hz/0W emitted high-confidence `off` with the incorrect `generator_voltage_absent` reason.
- Actual tracker replay: isolated input6 bits0 and1 each produced no journal observation.
- Independent event-correlation calculation:20 of22 covered nominal low-power windows retained selector2 throughout their saved samples; verified the additional02:35 raw EPS159.3V record and inspected the11:43 decoded37V bridge record.
- Actual production transport on localhost: preserved history payload before valid identity caused failure before any request. Synthetic envelope/peer; no device access and no physical-incident reproduction claimed.

No repository test suite, firmware rebuild, physical generator/load experiment or live deployment verification was run. Temporary replay resources were closed and removed. Original evidence and production code were unchanged.
