# EG4 host recorder MVP

| Field | Value |
| --- | --- |
| Initiative ID | `eg4-host-recorder` |
| Lifecycle | `implementing` |
| Source business need | [Conversation-captured problem](./2026-11-09-host-recorder-mvp.md#problem-statement) (`conversation-provisional`) |
| PRD | [EG4 host recorder MVP](./2026-11-09-host-recorder-mvp.md) |
| Issue board | [Implementation issue board](./issues/README.md) |

## Governing authority

| Role | Reference | Status |
| --- | --- | --- |
| Accepted host-only design and rollout | [Host recorder MVP specification](../../docs/host-recorder-mvp-spec.md) | governing |
| Acceptance contract | [Focused acceptance tests A1–A8](../../docs/host-recorder-mvp-spec.md#focused-acceptance-tests) | governing |
| Read-only acquisition surfaces | [Verified read surfaces](../../investigations/2026-11-08-firmware-reverse-engineering/direct-session/history-and-read-surfaces.md) | governing for listed read shapes, not installed firmware identity |
| Installed firmware identity / physical protection-bit semantics | See [PRD authority gaps](./2026-11-09-host-recorder-mvp.md#implementation-decisions) | missing; raw capture must preserve uncertainty |

## Initiative lifecycle

| Stage | Status | Artifact |
| --- | --- | --- |
| Business need captured | complete | [source](./2026-11-09-host-recorder-mvp.md#problem-statement) (`conversation-provisional`) |
| PRD synthesis | complete | [PRD](./2026-11-09-host-recorder-mvp.md) |
| Issue slicing | complete | [Issue board](./issues/README.md) |
| Delivery | current (`implementing`) | [EG4REC-06](./issues/06-deploy-and-verify-recorder.md) |
| Closure | pending | Delivery evidence after completion |
