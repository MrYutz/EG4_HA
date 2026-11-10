---
id: EG4REC-07
title: Archive one week of raw evidence and preserve incident snapshots on Plucky
type: AFK
status: done
phase: delivery
source_github: null
source_business_need: conversation-provisional
source_prd: prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md
governing_refs:
  - docs/host-recorder-mvp-spec.md
  - prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md#implementation-decisions
test_contract_refs:
  - docs/host-recorder-mvp-spec.md
  - prds/eg4-host-recorder/2026-11-09-host-recorder-mvp.md#testing-decisions
test_seam: Archive function and actual CLI with real local/SFTP files -> eight-day raw archive, preserved incident snapshots and existing analyzer
authority_gaps: []
blocked_by:
  - 06-deploy-and-verify-recorder.md
depends_on_story_ids:
  - 9
slice_order: 7
completion_evidence:
  implementation_commit: dec0820cef61b1032c13ba21ad4e667c4582b0f3
  red_evidence: /home/greg/.local/state/eg4-host-recorder/plucky-archive-delivery-2026-11-10/7.1-red-evidence.json
  green_evidence: /home/greg/.local/state/eg4-host-recorder/plucky-archive-delivery-2026-11-10/7.1-green-evidence.json
  alignment_verified: true
---

## What to build

One Plucky-side file-only archiver keeps at least one week of raw/controller logs and indefinitely preserves incident snapshots without adding network storage to HA's polling path. Backfill available files, then incrementally copy through the established SFTP route every minute. Keep unchanged sampling, an independent local outage buffer, original bytes/source identity and honest incomplete coverage. The later approved storage-pressure correction reduces only the three HA recorder ceilings after preserving current files and permits one safe Core restart.

## Acceptance criteria

- [x] R7/A9 and stable story9 remain the governing accepted scope; original six completed issues are not reopened or weakened.
- [x] Actual available raw/controller files backfill onto private Plucky storage; subsequent runs append only new source bytes and survive active-file rotation/restart without overwriting historical prefixes or duplicating a prefix.
- [x] Retain eight days after last newly received bytes and never delete recent/unrelated evidence or incident snapshots for a byte ceiling. Verify the boundary with controlled time and real temporary files.
- [x] Open incidents are honest mutable/incomplete copies. Stable finalized/interrupted manifests, sizes and available final hashes produce preserved snapshots that HA pruning or later source changes cannot overwrite; no automatic snapshot expiry.
- [x] Source disconnect/interrupted copy reports failure without destroying previous data or stalling HA. Resume catches up where local spool data still exists, and irrecoverable missing history is not fabricated.
- [x] Existing analyzer consumes archived raw/controller and incident directories. Real SFTP/CLI proofs, focused type/lint checks, final full suite and independent review cover consumer-visible errors and retention/custody behavior.
- [x] Push and verify coherent committed source before installing an isolated Plucky release and user timer. Confirm actual backfill, incremental scheduled activation, host-key verification, private permissions, custody hashes/status and continued HA capture with no equipment changes. For the separately approved storage-pressure correction, preserve/hash current files, change only256MiB raw/32MiB journal/128MiB incident ceilings with a private configuration backup and checked Core restart, leave unrelated files untouched and verify recovered persistence with explicit loss/gap evidence.
- [x] Publish operator/storage documentation and real deployment evidence outside Git. No keys/raw logs/user work enter commits; the existing credential has broader authority than this read-only client's behavior and is not claimed OS-sandboxed.

## Blocked by

- [06-deploy-and-verify-recorder.md](./06-deploy-and-verify-recorder.md) — completed prerequisite.

## Pull request stack

Root: `main` at `1d4714a63198b6196df00256279b88c176283f67`.

| Key | Branch | Purpose | Predecessor / base | Acceptance advanced |
| --- | --- | --- | --- | --- |
|7.1 | `codex/eg4-host-recorder-7.1-plucky-archive` | Complete file-only raw retention and incident preservation, local/SFTP verification, isolated Plucky timer deployment and exercised receipt | Root `main` | All EG4REC-07 criteria / R7 / A9 |

One vertical implementation entry, not separate storage/transport/operations layers. A later metadata-only completion record may record its actual merge after implementation acceptance; it adds no deployment. Preserve all six pre-existing modified source/test files and all untracked investigation/user artifacts. No HA component source is in this entry's ownership.

## Completion evidence

- Installed source `dec0820cef61b1032c13ba21ad4e667c4582b0f3`; acceptance/documentation head `12ddff235b985475f2078786844d875c7d985698`.
- [PR10](https://github.com/MrYutz/EG4_HA/pull/10) merged to `main` at `58f2a07158f09703ecbbdd656dfd1aba87587375` on2026-11-10T12:40:52Z. Remote head/base and complete incremental diff were verified; installed code hashes match the pushed source.
- Final workspace suite298passed/3skipped/3upstream warnings; archive/local/real-SFTP group54passed; source typing0errors and critical/import Ruff passed. Both independent review axes closed all findings on the installed source.
- Actual Plucky timer is enabled and successfully copied new live bytes on a subsequent minute activation. Eight-day raw/controller retention and permanent incident custody are verified offline; four real sealed incomplete snapshots plus one moving incident were preserved at the receipt time. The installed unchanged analyzer succeeded.
- Approved HA disk-pressure relief preserved46files/810,816,391bytes with remote hash matches before changing only the three storage ceilings and restarting Core. The recovered session had zero raw/journal drops/write errors at12:36:25UTC. Approximately73minutes of retained-source gap remain explicit; they are not a power-outage or engine-cycle measurement.
- [Deployment summary and limitations](../../../docs/plucky-recorder-archive-deployment-2026-11-10.md); durable private red/green indexes above contain the exercised evidence. The metadata-only completion record adds no runtime change or second implementation slice.
