# EG4 host recorder MVP

| Field | Value |
| --- | --- |
| Initiative ID | `eg4-host-recorder` |
| Lifecycle | `complete` |
| Source business need | [Conversation-captured problem](./2026-11-09-host-recorder-mvp.md#problem-statement) (`conversation-provisional`) |
| PRD | [EG4 host recorder MVP](./2026-11-09-host-recorder-mvp.md) |
| Issue board | [Implementation issue board](./issues/README.md) |

## Governing authority

| Role | Reference | Status |
| --- | --- | --- |
| Accepted host-only design and rollout | [Host recorder MVP specification](../../docs/host-recorder-mvp-spec.md) | governing |
| Acceptance contract | [Focused acceptance tests A1–A8](../../docs/host-recorder-mvp-spec.md#focused-acceptance-tests) and [Plucky A9](../../docs/host-recorder-mvp-spec.md) | governing |
| Read-only acquisition surfaces | [Verified read surfaces](../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/history-and-read-surfaces.md) | governing for listed read shapes, not installed firmware identity |
| Installed firmware identity / physical protection-bit semantics | See [PRD authority gaps](./2026-11-09-host-recorder-mvp.md#implementation-decisions) | missing; raw capture must preserve uncertainty |

## Initiative lifecycle

| Stage | Status | Artifact |
| --- | --- | --- |
| Business need captured | complete | [source](./2026-11-09-host-recorder-mvp.md#problem-statement) (`conversation-provisional`) |
| PRD synthesis | complete | [PRD](./2026-11-09-host-recorder-mvp.md) |
| Issue slicing | complete | [Issue board](./issues/README.md) |
| Delivery | complete | [Issue board](./issues/README.md) |
| Closure | current (`complete`) | Delivery evidence after completion |

## Delivery evidence

[Original HA production deployment receipt](../../docs/host-recorder-deployment-2026-11-10.md). The original six implementation issues remain done; source/review/production/merge evidence is recorded on EG4REC-06 and in its private evidence index.

[Plucky archive deployment and approved storage recovery](../../docs/plucky-recorder-archive-deployment-2026-11-10.md). EG4REC-07 is also done: PR10 merged at `58f2a07158f09703ecbbdd656dfd1aba87587375`, with the enabled timer, installed hashes, real transfers, recovery gaps and private evidence indexes recorded on the issue. All seven slices are complete; no delivery slice remains active.
