# EG4 host recorder implementation issue board

## Dependency-ordered slices

| Order | Title | Type | Status | Blocked by | Stories | Public seam | File |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | Preserve raw dongle evidence through invalid and interleaved responses | AFK | done | None | 1, 2, 7 | Dongle acquisition against loopback TCP -> persisted JSONL -> analyzer CLI | [EG4REC-01](./01-preserve-raw-dongle-evidence.md) |
| 02 | Make generator and charging states truthful through gaps | AFK | done | 01 | 4 | Logger samples/events and restored session state using acquisition identity and freshness | [EG4REC-02](./02-correct-generator-and-charge-state.md) |
| 03 | Keep healthy devices recording when another device stalls | AFK | done | 02 | 3, 7 | Integration polling lifecycle -> per-block records and existing diagnostic health attributes | [EG4REC-03](./03-isolate-device-polling.md) |
| 04 | Preserve protection changes and recover the journal after write failures | AFK | done | 01 | 5, 7 | Recorder append/observe/close against temporary storage -> persisted journal and health | [EG4REC-04](./04-recover-controller-journal-and-health.md) |
| 05 | Freeze, retain and reconstruct complete incident bundles | AFK | done | 03, 04 | 6, 7 | Recorder saved incident directories -> manifest, existing diagnostic health and streaming analyzer CLI | [EG4REC-05](./05-preserve-and-analyze-incident-bundles.md) |
| 06 | Push, deploy and verify the recorder on Home Assistant | AFK | in-progress | 05 | 8 | Actual HA API and installed recorder -> fixed live capture, isolated synthetic export and rollback evidence | [EG4REC-06](./06-deploy-and-verify-recorder.md) |

## Ready

None.

## In Progress

- [EG4REC-06 — Push, deploy and verify the recorder on Home Assistant](./06-deploy-and-verify-recorder.md)

## Done

- [EG4REC-01 — Preserve raw dongle evidence through invalid and interleaved responses](./01-preserve-raw-dongle-evidence.md)
- [EG4REC-02 — Make generator and charging states truthful through gaps](./02-correct-generator-and-charge-state.md)
- [EG4REC-03 — Keep healthy devices recording when another device stalls](./03-isolate-device-polling.md)
- [EG4REC-04 — Preserve protection changes and recover the journal after write failures](./04-recover-controller-journal-and-health.md)
- [EG4REC-05 — Freeze, retain and reconstruct complete incident bundles](./05-preserve-and-analyze-incident-bundles.md)
