---
id: TH3.E1.US7
title: "Control-plane preflight and guarded repair diagnostics"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Preflight reports root, schema, transition-guard, authoritative lock, quarantine, pending event, committed revision, ledger, queue, worker capability, and declared-path readiness without changing state."
  - AC2: "Read-only diagnosis distinguishes authoritative corruption from safe private or quarantined debris and identifies the exact explicit repair action."
  - AC3: "Guarded repair creates backups or quarantines, is idempotent, and refuses ambiguous or future-versioned state without mutation."
depends-on: [TH3.E1.US6]
---

As an operator, I want complete preflight and guarded repair diagnostics so that
a recovered cockpit proves its storage and capabilities before dispatching.

## Acceptance criteria

- [ ] AC1: Read-only preflight covers every control-store state.
- [ ] AC2: Diagnostics distinguish safe debris from authority.
- [ ] AC3: Explicit repairs are backed up, idempotent, and fail closed.

## BDD scenarios

### Happy path: healthy control plane is ready

Given valid roots, lock state, committed events, ledger, and worker capabilities
When preflight runs
Then it reports ready without changing any file.

### Edge case: legacy worker capability is detected

Given a valid quarantine remains from an interrupted cleanup
When preflight runs
Then it reports non-authoritative debris and the optional cleanup action.

### Error case: ambiguous authoritative state

Given lock identity or committed event continuity cannot be proven
When repair is requested
Then mutation remains blocked and no automatic deletion occurs.
