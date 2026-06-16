# TC Format Reference

Every test case in this test book follows this template. Copy-paste it when adding new
TCs, and keep the `@TC-XXX-NNN` tag identical between the chapter, `SUMMARY.md`, and the
Playwright `test()` title (the audit parser relies on that 1:1 cross-link).

---

### TC-XXX-NNN — {Short imperative title}

| Field      | Value |
|------------|-------|
| Priority   | P0 · P1 · P2 · P3 |
| Scope      | UI · API · UI+API |
| Tags       | e.g. smoke, happy-path |
| Automation | `tests/{domain}.spec.ts` |

#### Preconditions
- State required before the test (user role, data present, feature flag on/off).

#### Steps
1. Navigate to `/path`.
2. Click `[data-testid="btn-x"]`.
3. Type `"value"` into the field.

#### Expected Result
- Observable outcome (element visible, toast shown, URL changed).
- What does NOT happen (no 500, no redirect to login).

```gherkin
Feature: {Domain} — {Feature name}

  @TC-XXX-NNN @smoke
  Scenario: {Title}
    Given {precondition}
    When  I {action} {target}
    Then  {assertion}
    And   {assertion}
```

---

## Priority scale

| Priority | Meaning | Tag |
|----------|---------|-----|
| **P0** | Smoke / regression gate — must always pass | `@smoke` |
| **P1** | Major happy path | `@major` |
| **P2** | Minor interaction — forms, filters, error messages | `@minor` |
| **P3** | Micro / edge — empty states, boundaries, permission denials | `@micro` |

## Chapter prefixes

| Chapter | Prefix |
|---------|--------|
| CH01 Smoke | `TC-SMOKE-` |

> Add a prefix row for each new chapter. Keep prefixes short, upper-case, and unique.

## TC-ID rules

- Format: `TC-{CHAPTER}-{NNN}` where `NNN` is a zero-padded sequence (`001`, `002`, …).
- The tag in the spec title is the same id prefixed with `@`: `@TC-SMOKE-001`.
- One `test()` ↔ one TC-ID. Split a test if it would cover two TCs.
- The priority tag (`@smoke`/`@major`/`@minor`/`@micro`) is added alongside the TC tag.
