# ADR-003 — Bootstrap entry-point design

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`copilotcockpit` performs two clearly distinct jobs (VP1 §5):

- **(a) global** — install/update the seven skills + `cockpit-wake` into the user's
  home (`~/.copilot/skills/`, `~/.local/bin/`);
- **(b) e2e** — scaffold (or `--update`) an `e2e/` sub-repo into a target project.

How should these be exposed on the command line? The two natural shapes are:

1. **A single dispatcher** — `./bootstrap.sh <subcommand> [args]`
   (`global`, `e2e <dir>`, plus helpers like `doctor`).
2. **Separate scripts** — `./bootstrap-global.sh` and `./bootstrap-e2e.sh <dir>`.

Forces:
- **Discoverability (NFR-8):** a developer who finds the repo should succeed without
  reading docs. One obvious entry point with `--help` is easier to find than guessing
  which of several scripts to run.
- **Composability:** scripted/CI callers want stable, greppable invocations.
- **Idempotency guarantees (NFR-1):** shared concerns — OS detection, logging,
  backup-before-overwrite, dry-run — should be implemented once, not duplicated.
- **Simplicity (P1):** avoid a heavyweight CLI framework.

## Decision

**A single entry point — `bootstrap.sh` — acting as a thin dispatcher, backed by
one `lib/cmd-*.sh` implementation file per subcommand and a shared `lib/common.sh`.**

```
./bootstrap.sh                      # no args → prints usage
./bootstrap.sh global [--link] [--dry-run]
./bootstrap.sh e2e <target-dir> [--update] [--no-git] [--yes] [--dry-run]
./bootstrap.sh doctor               # verify prerequisites + report install/drift state
./bootstrap.sh --help | -h
```

- `bootstrap.sh` parses the subcommand, sources `lib/common.sh` (logging, OS
  detection, idempotency/backup helpers, `--dry-run`), and delegates to
  `lib/cmd-<subcommand>.sh`.
- Shared cross-cutting behaviour (NFR-1 idempotency, NFR-2 cross-platform shims,
  `--dry-run`) lives **once** in `lib/common.sh` and is inherited by every subcommand.
- Each subcommand supports `--dry-run` to print what it *would* do, reinforcing the
  "safe to run repeatedly" promise.

This is a plain-bash dispatcher (a `case "$1"` over subcommands) — **no CLI
framework**, honouring P1.

## Consequences

### Positive
- One discoverable, self-documenting entry point (NFR-8); no-arg invocation teaches usage.
- Cross-cutting idempotency/backup/dry-run logic is written once in `common.sh` (NFR-1).
- Composable for CI: `./bootstrap.sh e2e <dir> --update --yes` is a stable, greppable call.
- Easy to extend (`doctor` today; `migrate`/`status` later) without new top-level scripts.

### Negative / Trade-offs
- A dispatcher adds one indirection layer vs. calling a script directly. Mitigation:
  the layer is ~30 lines of `case` and is itself self-documenting.
- All paths funnel through one file, so a bug in the dispatcher affects every command.
  Mitigation: the dispatcher is trivial and covered by the TH1 smoke spike.

### Risks
- Subcommand sprawl over time. Mitigation: keep the surface minimal
  (`global`, `e2e`, `doctor`) and require an ADR to add a new top-level verb.

## Alternatives Considered

### A. Two separate scripts (`bootstrap-global.sh` + `bootstrap-e2e.sh`)
- Pros: dead simple; each script does one thing; no dispatcher layer.
- Cons: shared idempotency/OS/backup logic must be duplicated or pulled into a sourced
  lib anyway (so you end up with a `lib/` regardless); two entry points to discover;
  no obvious "front door" for a newcomer.
- **Rejected because** it duplicates cross-cutting logic and weakens discoverability
  (NFR-8) for no real simplicity gain — you need `lib/` either way.

### B. A real CLI framework (e.g. a Node/Python `click`-style CLI)
- Pros: rich help, argument validation, subcommands for free.
- Cons: introduces a runtime/build dependency the harness otherwise does not need,
  violating P5 (minimal deps) and P1 (simplicity).
- **Rejected because** a bash `case` dispatcher meets every requirement with zero new
  dependencies.

## History
- 2026-06-16: Proposed
