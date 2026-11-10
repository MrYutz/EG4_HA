---
id: EG4REC-07
title: Archive one week of raw evidence and preserve incident snapshots on Plucky
type: AFK
status: in-progress
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
  implementation_commit: null
  red_evidence: null
  green_evidence: null
  alignment_verified: false
---

## What to build

One Plucky-side file-only archiver keeps at least one week of raw/controller logs and indefinitely preserves incident snapshots without adding network storage to HA's polling path. Backfill available files, then incrementally copy through the established SFTP route every minute. Keep unchanged sampling and the local outage buffer, original bytes/source identity and honest incomplete coverage.

## Acceptance criteria

- [ ] R7/A9 and stable story9 remain the governing accepted scope; original six completed issues are not reopened or weakened.
- [ ] Actual available raw/controller files backfill onto private Plucky storage; subsequent runs append only new source bytes and survive active-file rotation/restart without overwriting historical prefixes or duplicating a prefix.
- [ ] Retain eight days after last newly received bytes and never delete recent/unrelated evidence or incident snapshots for a byte ceiling. Verify the boundary with controlled time and real temporary files.
- [ ] Open incidents are honest mutable/incomplete copies. Stable finalized/interrupted manifests, sizes and available final hashes produce preserved snapshots that HA pruning or later source changes cannot overwrite; no automatic snapshot expiry.
- [ ] Source disconnect/interrupted copy reports failure without destroying previous data or stalling HA. Resume catches up where local spool data still exists, and irrecoverable missing history is not fabricated.
- [ ] Existing analyzer consumes archived raw/controller and incident directories. Real SFTP/CLI proofs, focused type/lint checks, final full suite and independent review cover consumer-visible errors and retention/custody behavior.
- [ ] Push and verify coherent committed source before installing an isolated Plucky release and user timer. Confirm actual backfill, incremental scheduled activation, host-key verification, private permissions, custody hashes/status and healthy continued HA capture with no restart or equipment changes.
- [ ] Publish operator/storage documentation and real deployment evidence outside Git. No keys/raw logs/user work enter commits; the existing credential has broader authority than this read-only client's behavior and is not claimed OS-sandboxed.

## Blocked by

- [06-deploy-and-verify-recorder.md](./06-deploy-and-verify-recorder.md) — completed prerequisite.

## Pull request stack

Root: `main` at `1d4714a63198b6196df00256279b88c176283f67`.

| Key | Branch | Purpose | Predecessor / base | Acceptance advanced |
| --- | --- | --- | --- | --- |
|7.1 | `codex/eg4-host-recorder-7.1-plucky-archive` | Complete file-only raw retention and incident preservation, local/SFTP verification, isolated Plucky timer deployment and exercised receipt | Root `main` | All EG4REC-07 criteria / R7 / A9 |

One vertical implementation entry, not separate storage/transport/operations layers. A later metadata-only completion record may record its actual merge after implementation acceptance; it adds no deployment. Preserve all six pre-existing modified source/test files and all untracked investigation/user artifacts. No HA component source is in this entry's ownership.
