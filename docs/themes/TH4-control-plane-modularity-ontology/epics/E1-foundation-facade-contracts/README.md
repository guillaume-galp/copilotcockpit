# TH4.E1: Foundation Facade and Typed Contracts

## Goal

Create the safe migration foundation before moving behavior: pin the public
`cockpit_control` compatibility surface, introduce package/module seams, and
define typed contracts plus dependency rules that later stories must follow.

## Stories

- TH4.E1.US1 — Compatibility facade and public import contract
- TH4.E1.US2 — Typed contracts and dependency-boundary rules

## Completion gate

Existing imports and CLI entry points are characterized, no behavior has moved
without tests, the installed Python module layout and managed runtime artifact
set are documented across install/doctor/uninstall/release/cold-install
surfaces, and future module dependencies have an enforceable acyclic shape.
