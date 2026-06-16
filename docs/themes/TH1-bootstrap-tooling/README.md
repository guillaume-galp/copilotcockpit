# TH1 — Bootstrap Tooling

> Theme of [VP1 — e2e-bootstrap](../../vision_of_product/VP1-e2e-bootstrap/VP1.md)
> · Architecture: [overview](../../architecture/overview.md)
> · Status: not_started · Date: 2026-06-16

Delivers the `copilotcockpit` bootstrap toolkit: the canonical, one-command path from a
cold project to a fully-wired, self-auditing Playwright E2E cockpit — a global skills
install (`bootstrap.sh global`) and a per-project scaffold (`bootstrap.sh e2e <dir>`),
plus the developer-facing release/test machinery for the repo itself.

## Epics

| Epic | Title | Stories |
|------|-------|---------|
| E1 | Core bootstrap shell — dispatcher, shared lib, doctor | 3 |
| E2 | Global skills install — vendor skills + cockpit-wake, `cmd-global`, cold install | 4 |
| E3 | E2E scaffold — `templates/e2e/`, `MANIFEST.toml`, scaffold + `--update` | 6 |
| E4 | CI/CD & releases — `VERSION`/`CHANGELOG`, `release.yml`, `ci.yml` | 3 |
| E5 | Developer skill & test infra — `run-tests.sh`, test suites, `copilotcockpit-dev` | 3 |
| E6 | Spikes — macOS/BSD portability, `MANIFEST.toml` classification | 2 |

**Total: 21 stories.**

## Sequencing

E6 spikes are scheduled first (their findings feed E1 `common.sh` and E3 templates /
`MANIFEST.toml`). E1 (`common.sh` + dispatcher) gates E2 and E3. E5 test infra follows
the commands it tests; E4 `ci.yml` wires the test suites in; `release.yml` consumes the
template tarball contract. See `docs/plan/backlog.yaml` for the full `depends_on` graph.

## Source-of-truth artefacts

- Vision: VP1 §5 (two-phase bootstrap), §10 (Definition of done)
- ADRs: ADR-001…ADR-008 (all `Proposed`)
- Architecture: §2 (repo tree), §4–§9 (phases, parameterisation, idempotency, CI/CD, dev workflow)
