# TH2: FIFO Overseer Request Queue

## Goal

Deliver the cockpit-owned queue operator and overseer workflow that accepts
build-method ideas, processes them FIFO, coordinates workers one item at a time,
and clears each item only after local delivery plus governed E2E evidence.

## Definition of Done

- Queue operator MVP supports enqueue, list, inspect, classify, pause, resume,
  reject, start-next, and clear-current.
- Queue state persists as `docs/queue/items/<id>.yaml` plus
  `docs/queue/events.jsonl`.
- Overseer skill/runbook documents how to process queued items without
  interrupting active worker missions.
- worker-test / e2e-operator gating is part of the clear-current workflow.
- Tests cover queue validation, FIFO ordering, and transition logging.
