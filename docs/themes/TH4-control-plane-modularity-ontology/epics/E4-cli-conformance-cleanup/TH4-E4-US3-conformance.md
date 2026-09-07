# TH4.E4.US3 Final Conformance Packet

This packet is the final acceptance evidence for:

- **story:** `TH4.E4.US3`
- **ADR boundary:** [ADR-020](../../../../ADRs/ADR-020-incremental-control-plane-module-extraction.md)
- **theme:** TH4 control-plane modularity and ontology adoption

It does not introduce runtime features. It only records and verifies that TH4
preserved behavior and modularity constraints.

## Focused conformance checks

Run from repository root:

```bash
tests/unit/check-th4-e4-us3-conformance.sh
```

Coverage in this focused gate:

- managed runtime module import compatibility;
- dependency direction and no circular imports across managed modules;
- wrapper/facade import boundary (`cockpit-control`, `cockpit-overseer`,
  `cockpit-wake`);
- CLI help contract sentinel commands;
- schema/validation, lock/journal/projection, lifecycle/commands/mission,
  controller/wake seam tests;
- interruption and resilience checks crossing ADR-018/ADR-019 boundaries.

Then run full repository gate:

```bash
./run-tests.sh all
```

## Dependency direction and bounded-context ownership

Managed modules remain aligned to the ADR-020 direction:

```text
cli/rendering/adapters -> controller/mission_control/wake
                          -> commands/lifecycle/projection
                          -> journal/locks/root_schema
```

Conformance tests fail if:

- a lower-layer module imports an upper-layer adapter module;
- any managed runtime module import cycle appears;
- wrapper boundaries stop using the `cockpit_control` facade.

## Legacy compatibility aliases accepted during TH4

TH4 keeps established public names when compatibility requires them. These are
documented aliases, not speculative renames.

| Stable public name | Canonical ownership context | Why retained |
|---|---|---|
| `cockpit_control` module re-exports | compatibility facade | existing imports and wrappers depend on this boundary |
| `repair-lock`, `repair-store` CLI verbs | lock/store diagnostic operations | accepted command contracts preserved |
| `_print_error`, `_session_identity` facade call points | rendering/tmux adapter seams | internal bridge points retained for facade compatibility |

Additional legacy-name policy is documented in
[facade-compatibility](../../../../architecture/facade-compatibility.md).

## Rollback guidance

The stable rollback point remains the **`cockpit_control` facade**.

Rollback for TH4 conformance issues:

1. restore previous facade re-export wiring and wrapper imports;
2. restore previous `MANAGED_RUNTIME_MODULES` entries if needed;
3. rerun focused conformance and `./run-tests.sh all`.

**No control-store data migration** is introduced by TH4 extraction/conformance.
State files (`control.json`, committed events, `ledger.json`, `events.jsonl`,
locks, wake state) stay schema-compatible and replayable.
