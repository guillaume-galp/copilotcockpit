# TH2 Changelog — FIFO Overseer Request Queue

## Epic TH2.E1 — Queue operator

### Stories Completed

- TH2.E1.US1 — Queue intake and classification
- TH2.E1.US2 — Queue persistence and CLI operations

### Key Changes

- Added `bin/cockpit-queue`.
- Added queue commands for classify, enqueue, list, inspect, pause, resume,
  reject, start-next, clear-current, and internal state transition support.
- Wired `cockpit-queue` into global install, doctor, uninstall, and smoke tests.
- Added unit coverage for intake, rejection, FIFO ordering, pause/resume, and
  clearance evidence.

## Epic TH2.E2 — Cockpit E2E operations

### Stories Completed

- TH2.E2.US1 — Overseer FIFO worker loop
- TH2.E2.US2 — E2E operator clearance gate

### Key Changes

- Updated `e2e-cockpit` with FIFO queue operations and clearance rules.
- Updated `e2e-operator` report format with queue clearance evidence.
- Documented that E2E operator gating is queue-scoped cockpit behavior.
