# ADR-017: Additive Control-Plane Adoption and Migration

## Status

Accepted

## Context

Existing cockpits already have queue items, wake schedules, trace archives,
worker overlays, and project-owned launchers. VP3 must improve control without
destroying those artefacts or violating template ownership.

## Decision

Adopt VP3 additively:

- preserve ADR-010 queue storage and existing queue items;
- keep existing trace archives readable as diagnostic evidence;
- identify direct-message recurrent wakes as legacy and provide explicit
  migration to controller ticks;
- expose legacy workers as `legacy-observed`; allow current completion but
  require lifecycle capability for cancellation/replacement;
- install updated managed binaries idempotently through the existing bootstrap;
- preserve project-owned templates and overlays according to `MANIFEST.toml`;
- back up canonical control files before schema migration;
- refuse mutation of unknown future schema versions.

Doctor/preflight reports compatibility state and required operator action. It
does not silently rewrite project-owned cockpit configuration.

## Consequences

### Positive

- Existing projects can adopt VP3 incrementally.
- Queue and audit history remain intact.
- Bootstrap update behavior remains non-destructive.

### Negative

- A transition period supports both legacy observation and VP3 lifecycle state.
- Some project-owned launchers require guided manual updates.

### Risks

- Indefinite legacy mode would weaken guarantees; planning must include a clear
  migration and deprecation path.

## Alternatives Considered

### Replace existing cockpit state in place

Rejected because it risks losing schedules, evidence, and project
customizations.

### Maintain two permanent control-plane implementations

Rejected because it would double protocol complexity and produce divergent
behavior.
