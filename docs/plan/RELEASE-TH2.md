# Release: TH2 FIFO Overseer Request Queue

## Summary

TH2 adds a cockpit-owned FIFO request queue for overseers. Build-method requests
can now be classified, persisted, processed one at a time, and cleared only after
local delivery plus queue-scoped E2E runbook evidence or an explicit waiver.

## Epics Delivered

- TH2.E1 — Queue operator
- TH2.E2 — Cockpit E2E operations

## Breaking Changes

- None.

## Migration Notes

- Install/update global cockpit tools to receive `cockpit-queue`.
- Queue state defaults to `docs/queue/items/<id>.yaml` plus
  `docs/queue/events.jsonl`; use `COCKPIT_QUEUE_ROOT` to override in tests or
  isolated sessions.
