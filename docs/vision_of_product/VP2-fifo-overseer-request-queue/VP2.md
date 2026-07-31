# VP2: FIFO Overseer Request Queue

## Problem statement

Human operators and product owners often generate bursts of build ideas while a
cockpit is already delivering an active VP/theme. Today those ideas either
interrupt the current orchestration or remain orphaned in chat context. The
overseer needs a durable FIFO queue so build-method-tagged ideas can be accepted,
classified, delivered one at a time, tested through the governed E2E runbook, and
cleared only when complete.

## Target users and personas

- **Human operator / product owner**: submits ideas at natural thought speed
  without pressuring the current worker loop.
- **Overseer**: regulates throughput, keeps workers focused on one item at a
  time, and reports queue state.
- **worker-test**: runs the governed E2E runbook and classifies failures before
  an item clears.
- **worker-dev / worker-fix**: receive one scoped mission at a time from the
  current queue item.

## Core features

- Queue operator command surface: `enqueue`, `list`, `inspect`, `classify`,
  `pause`, `resume`, `reject`, `start-next`, and `clear-current`.
- FIFO processing by default; reprioritization requires explicit human override.
- Queue eligibility rules: accept `/the-copilot-build-method` requests and
  explicit overseer-approved build work; reject questions, pure diagnostics,
  unrelated commands, and non-build-method ideas.
- Queue states:
  `queued -> shaping -> planned -> implementing -> testing -> fixing -> delivered -> e2e-testing-runbooks -> e2e-related-fixing -> cleared`,
  with `blocked` and `rejected` escape states.
- Queue persistence using `docs/queue/items/<id>.yaml` plus append-only
  `docs/queue/events.jsonl`.
- Overseer status reports: current item, queue depth, state, assigned worker,
  latest test/runbook result, blockers, and next FIFO item.
- Queue-scoped E2E operator chaining: queue completion requires worker-test to
  run the governed runbook and report evidence before clearing.

## Success criteria

- No accepted build-method idea is orphaned.
- FIFO order is preserved unless the human explicitly overrides it.
- Every cleared item has local delivery evidence plus governed E2E runbook
  evidence.
- The overseer stays idle less often when queue items are available.
- Queue reads are token-efficient: the overseer can inspect one item without
  loading the entire queue history.

## Constraints

- `copilotcockpit` owns the executable queue operator and cockpit overseer
  workflow.
- `copilotautopilot` owns the separate `gitflow-operator`; queue storage,
  clearance, and E2E operator gating remain `copilotcockpit` scope.
- One active queue item should map to one active worker mission per worker.
- Multi-repo queue items may create one feature/fix branch per affected repo,
  but branch orchestration is delegated to the `gitflow-operator` capability.
- The queue must remain lightweight in CPU and memory use and reviewable in Git.

## Open questions

- Which queue commands should be implemented first for an MVP?
- Should queue IDs be sequential, timestamp-based, or content-addressed?
- How should a paused queue interact with already-running worker missions?
- How should queue events reference cockpit trace IDs and E2E run IDs?
