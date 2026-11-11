# Fast-poll feasibility test, 2026-11-11

Read-only (function 04) test of back-to-back polling on one held-open TCP
connection per dongle, run while the Home Assistant EG4 entry was paused.
Results and interpretation: `../adversarial-review-2026-11-11.md`, section O.

- `fast-poll-result.json`: every sample from both phases.
- `pause-session.json`: pause, test and restore timestamps; no credentials.
- `fast_poll_run.py`: the poller (named `fast_poll_test.py` when it ran;
  renamed so the test runner does not collect it).
- `pause_test_restore.py`: pause, watchdog, test, restore, verify. Kept
  local; it is not in the repository because it names where credentials live.
- `fast_poll_probe.py`, `gridboss-568x21-run2.json`: the earlier attempt made
  while the recorder was connected; the dongle closed the connection.

Do not run these while the EG4 entry is loaded.
