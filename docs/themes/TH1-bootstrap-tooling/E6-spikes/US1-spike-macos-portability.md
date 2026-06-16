# US1 — SPIKE: macOS/BSD portability of bootstrap bash scripts

| Field | Value |
|-------|-------|
| Epic | E6 — Spikes |
| Theme | TH1 — Bootstrap Tooling |
| Status | done |
| Size | XS |

## As a…
**As a** bootstrap toolkit author, **I want** a verified cheat-sheet of the BSD/GNU
shell-tool differences that affect our scripts, **so that** `lib/common.sh`,
`run-audit.sh`, and every generated script run identically on Linux and macOS (NFR-2).

## Acceptance Criteria
- [ ] AC1: A findings note is produced at
  `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/portability-cheatsheet.md`
  enumerating each portability hazard and the chosen portable construct.
- [ ] AC2: The note explicitly covers, at minimum: in-place edit (`sed -i` GNU vs
  `sed -i ''` BSD), `date` formatting (`date -d` GNU vs `date -r`/`-v` BSD),
  `stat` (`stat -c` GNU vs `stat -f` BSD), `readlink -f` (absent on macOS),
  `mktemp` template differences, and `grep -P`/`grep -o` availability.
- [ ] AC3: For each hazard the note states a **single portable recommendation**
  (e.g. "write to a temp file and `mv`, never `sed -i`"; "use `python3` for date math").
- [ ] AC4: The note records which version of each tool was tested
  (`sed --version`/`sed` BSD, `bash --version`) and on which OS.
- [ ] AC5: A one-line summary of each recommendation is suitable for copy into the
  `## Notes` of E1 and E3 implementation stories.

## BDD Scenarios

```gherkin
Feature: Portability spike findings

  Scenario: A portable in-place edit recommendation is captured
    Given the spike author tests `sed -i` on both GNU and BSD sed
    When  they record the divergent behaviour
    Then  the cheat-sheet recommends a single construct that works on both
    And   the recommendation avoids `sed -i` entirely (temp-file + mv)

  Scenario: Each hazard has a verdict
    Given the cheat-sheet lists date, stat, readlink, mktemp, grep, sed
    When  a reviewer reads any row
    Then  it names the GNU form, the BSD form, and the chosen portable form
```

## Notes
- Time-boxed investigation — **no production code** is delivered by this story, only
  the findings note.
- Reference: architecture §11 (Risks & spikes), NFR-2.
- This spike must complete **before** E1-US1 (`common.sh`) and E3-US1 (template runners)
  begin — its recommendations are inputs to their `## Notes`.
- Output location: create the `findings/` subdir under the epic and commit the cheat-sheet.
