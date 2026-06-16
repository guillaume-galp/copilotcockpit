# US3 — `skills/copilotcockpit-dev/SKILL.md`: GitOps delivery runbook

| Field | Value |
|-------|-------|
| Epic | E5 — Developer skill & test infra |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As an** agent developing `copilotcockpit` itself, **I want** a canonical in-repo
runbook for the feature→release pipeline, **so that** changes are delivered with a
disciplined, auditable, bounded-autonomy GitOps flow (ADR-008, architecture §9).

## Acceptance Criteria
- [ ] AC1: `skills/copilotcockpit-dev/SKILL.md` exists as the **8th** repo-managed
  skill, is valid Markdown with parseable frontmatter (non-empty `name:`/`description:`,
  so it passes Category-3 lint), and is enumerated by `cmd-global.sh` (E2-US3).
- [ ] AC2: It encodes the branch & commit conventions: work on `feature/<slug>` /
  `fix/<slug>` from latest `main`; never commit to `main` except the version-bump
  commit; Conventional Commits enforced and validated before push.
- [ ] AC3: It includes the commit-type → version-bump table (`feat`→minor,
  `fix`/`perf`→patch, `!`/`BREAKING CHANGE:`→major, `chore`/`docs`/`refactor`/`test`/
  `ci`→none).
- [ ] AC4: It documents the five test categories and the `run-tests.sh` local gate
  (categories 1–4 pre-commit; category 5 in release).
- [ ] AC5: It contains the explicit 12-step flow (Phase 1 branch/test/PR → Phase 2 merge
  gating → Phase 3 version bump + tag → Phase 4 release confirmation) with the exact
  `gh` commands (`gh pr create`, `gh pr checks --watch`, `gh run watch`,
  `gh run view --log-failed`, `gh pr merge --squash --auto`, `gh release view`).
- [ ] AC6: It states the bounded-autonomy escalation policy: **3 auto-fix attempts** at
  any failing gate, then stop, summarise, and raise to the human — no silent loops.
- [ ] AC7: It names `VERSION` as the single source of semver truth and describes the
  `CHANGELOG.md` prepend step that feeds `release.yml`.

## BDD Scenarios

```gherkin
Feature: copilotcockpit-dev skill

  Scenario: The skill installs as the 8th global skill
    Given skills/copilotcockpit-dev/SKILL.md exists
    When  "bootstrap.sh global" runs
    Then  ~/.copilot/skills/copilotcockpit-dev/SKILL.md is installed

  Scenario: The bump mapping is unambiguous
    When  an agent reads the skill for a "feat:" commit
    Then  it derives a minor version bump

  Scenario: Escalation is bounded
    Given a CI gate fails repeatedly
    When  the agent follows the runbook
    Then  after 3 auto-fix attempts it escalates to the human

  Scenario: The skill passes skills lint
    When  Category-3 lint runs over skills/*/SKILL.md
    Then  copilotcockpit-dev/SKILL.md passes (valid frontmatter)
```

## Notes
- Reference: ADR-008 (full 12-step flow, bump table, escalation, `gh` commands,
  `VERSION`/`CHANGELOG` ownership), architecture §9.
- Depends on E4-US2 (`release.yml`) existing so Phase 4 references a real workflow; it
  also assumes `VERSION`/`CHANGELOG.md` (E4-US1) and `run-tests.sh` (E5-US1) exist.
- This is the 8th skill that `cmd-global.sh` (E2-US3) installs — once it lands, the
  Category-4 "lists 8 skills" assertion (E5-US2) is fully satisfied.
- Path: `skills/copilotcockpit-dev/SKILL.md`.
