# US4 — Cold install: `install.sh` wrapper + `global --from-release <ref>`

| Field | Value |
|-------|-------|
| Epic | E2 — Global skills install |
| Theme | TH1 — Bootstrap Tooling |
| Status | pending |
| Size | M |

## As a…
**As a** new teammate on a fresh machine with no clone, **I want** a single one-liner
that fetches, verifies, and runs the installer, **so that** I get the harness without
cloning the repo (VP1 §6 step 1, ADR-007, NFR-3, NFR-8).

## Acceptance Criteria
- [ ] AC1: `install.sh` exists at the repo root, passes `bash -n`, and is a tiny
  fetch-verify-extract-run wrapper that performs **no other logic**.
- [ ] AC2: `install.sh` downloads `copilotcockpit.tar.gz` + its `.sha256` from the
  `releases/latest/download/` redirect, **verifies the checksum** (aborts on mismatch),
  extracts, then `exec`s `./copilotcockpit/bootstrap.sh global "$@"`.
- [ ] AC3: `bootstrap.sh global` gains a `--from-release <ref>` flag handled in
  `cmd-global.sh`: `latest` resolves the latest release tarball; `vX.Y.Z` pins to that
  tag's asset; the tarball + `.sha256` are downloaded and verified before extraction.
- [ ] AC4: After extracting a release tarball, `--from-release` runs the normal install
  pass (E2-US3) from the extracted directory.
- [ ] AC5: When run **inside a local clone with no `--from-release`**, no network call
  is made (local files are used — the ADR-007 default).
- [ ] AC6: A checksum mismatch or download failure aborts with a clear error and
  non-zero exit, leaving no partial install (atomic — NFR-1).
- [ ] AC7: `latest` resolution honours `GH_TOKEN` if present (rate-limit mitigation) but
  the documented one-liner uses the redirect (no API call).

## BDD Scenarios

```gherkin
Feature: Cold install path

  Scenario: One-liner installs from latest release
    Given a published release tarball + .sha256 exist
    When  the user runs install.sh
    Then  the tarball is downloaded and its sha256 verified
    And   bootstrap.sh global runs from the extracted dir

  Scenario: Checksum mismatch aborts safely
    Given a tampered tarball whose sha256 does not match
    When  install.sh verifies it
    Then  the install aborts with a non-zero exit
    And   nothing is installed

  Scenario: Pin to an explicit version
    When  the user runs "bootstrap.sh global --from-release v1.2.3"
    Then  the v1.2.3 tarball asset is fetched and verified

  Scenario: Inside a clone, no network is used
    Given the script runs inside a local clone with no --from-release
    When  global runs
    Then  local files are used and no download occurs
```

## Notes
- Reference: ADR-007 (release tarball, `install.sh`, `--from-release`, integrity),
  architecture §8.
- Depends on `cmd-global.sh` (E2-US3). The tarball **format/contents** are produced by
  `release.yml` (E4-US2); this story consumes that contract (`copilotcockpit/` holding
  `bootstrap.sh lib/ skills/ bin/ templates/ README.md`). `install.sh` itself is shipped
  as a release asset by E4-US2.
- Paths: `install.sh` (root) + `--from-release` handling in `lib/cmd-global.sh`.
