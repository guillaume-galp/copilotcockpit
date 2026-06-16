# US1 — Vendor the 7 harness skill source files into `skills/`

| Field | Value |
|-------|-------|
| Epic | E2 — Global skills install |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | S |

## As a…
**As a** harness maintainer, **I want** the seven canonical E2E-harness `SKILL.md`
playbooks committed in this repo, **so that** the repo is the single versioned source
of truth from which `global` installs and upgrades flow (ADR-001, NFR-4).

## Acceptance Criteria
- [ ] AC1: Exactly these seven files exist with non-empty content:
  `skills/e2e-cockpit/SKILL.md`, `skills/e2e-operator/SKILL.md`,
  `skills/setup-e2e-cockpit/SKILL.md`, `skills/setup-e2e-runbook/SKILL.md`,
  `skills/worker-dev/SKILL.md`, `skills/worker-fix/SKILL.md`,
  `skills/worker-test/SKILL.md`.
- [ ] AC2: Each `SKILL.md` is valid Markdown and carries parseable frontmatter with
  non-empty `name:` and `description:` keys (so it later passes E5-US2 skills lint).
- [ ] AC3: The files are vendored verbatim from the current
  `~/.copilot/skills/<role>/SKILL.md` contents (the existing working playbooks), with
  no behavioural edits in this story.
- [ ] AC4: `e2e-cockpit/SKILL.md` is the overseer playbook (its body addresses the
  overseer role) — there is **no** `worker-overseer` skill directory.
- [ ] AC5: No other skill directories are added here (the 8th, `copilotcockpit-dev`,
  is delivered by E5-US3; the general Build-Method skills are **not** vendored).

## BDD Scenarios

```gherkin
Feature: Vendored harness skills

  Scenario: All seven harness skills are present
    Given the repo is checked out
    When  I list skills/*/SKILL.md
    Then  exactly the seven harness roles are present
    And   each file is non-empty

  Scenario: Frontmatter is lint-ready
    Given any skills/<role>/SKILL.md
    When  its frontmatter is parsed as YAML
    Then  name and description are present and non-empty

  Scenario: No overseer worker skill exists
    When  I look for skills/worker-overseer/
    Then  it does not exist (e2e-cockpit is the overseer skill)
```

## Notes
- Reference: ADR-001 (skills source-of-truth; vendor from `~/.copilot/skills/`),
  architecture §2, §4. VP1 §5 skill list.
- Source the verbatim content from the developer's existing
  `~/.copilot/skills/<role>/SKILL.md`.
- These are prerequisites for `cmd-global.sh` (E2-US3) which copies them on install.
