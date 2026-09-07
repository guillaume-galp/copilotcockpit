# TH4.E4: CLI Thinning and Conformance Cleanup

## Goal

Move CLI parsing, rendering, and adapters last; clean up compatibility only when
callers are deliberately migrated; and prove the final modularity, dependency,
vocabulary, and integration conformance for TH4.

## Stories

- TH4.E4.US1 — CLI parsing, rendering, and adapter thinning
- TH4.E4.US2 — Compatibility facade cleanup and caller migration
- TH4.E4.US3 — Dependency, cycle, vocabulary, and integration conformance

## Completion gate

The CLI is thin, the facade remains intentionally compatible, dependency checks
are acyclic, ontology terminology is aligned in touched files, and the final
theme gate proves behavior preservation without speculative redesign.
