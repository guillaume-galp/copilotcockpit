# Changelog — copilotcockpit (orchestration)

Per-epic delivery log produced by the Autopilot Orchestrator during TH1 execution.

---

## Epic E6 — Spikes (portability + manifest classification)

**Stories Completed:** TH1-E6-US1, TH1-E6-US2 (both XS spikes, self-review only).

**Key Changes:**
- Verified macOS/BSD ↔ GNU shell-tool portability hazards and chose portable
  constructs (avoid `sed -i`; use `python3` for date math; `grep -Eo` not `-P`;
  temp-file+`mv`; uutils-coreutils caveat noted). Gates E1/E3 implementation.
- Proved a pure-bash `[[ str == glob ]]` array-driven matcher for MANIFEST.toml
  ownership classification (seed→framework→project precedence; unclassified →
  `project`/never-touch). Verified macOS bash-3.2-safe (single `*` matches `/`
  inside `[[ ]]`, no globstar dependency). 11/11 fixture classifications correct.

**Files Modified:**
- `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/portability-cheatsheet.md` (new)
- `docs/themes/TH1-bootstrap-tooling/E6-spikes/findings/manifest-classification.md` (new)

**Ceremony:** Small epic (2 stories). No test suite exists yet — suite run deferred to E5. Working tree clean; no fixtures leaked into VCS.

---

## Epic E1 — Core bootstrap shell (dispatcher, shared lib, doctor)

**Stories Completed:** TH1-E1-US1, TH1-E1-US2, TH1-E1-US3 (all reviewer-APPROVED).

**Key Changes:**
- `lib/common.sh` — sourceable shared library: stderr logging (`log_info/warn/error/ok`),
  `detect_os`, portable shims (`cc_realpath`, `cc_timestamp`, `cc_files_identical`),
  `cc_install_file` (identical→no-op, differ→backup `.bak-<ts>`+overwrite, dry-run aware),
  `cc_run` + `DRY_RUN`, idempotency guard. Portable per E6-US1 (no `sed -i`/`readlink -f`/`date -d`), bash 3.2-safe.
- `bootstrap.sh` — thin ADR-003 dispatcher: `global`/`e2e`/`doctor` routing via `case`,
  portable self-dir resolution, usage text, global `--dry-run` → `DRY_RUN`.
- `lib/cmd-doctor.sh` — prerequisite probes (found/missing+version), PATH check with
  exact `export PATH=…` remediation, 4-state skills + cockpit-wake drift detection,
  correct hard-vs-optional exit-code logic. Graceful when `skills/`/`bin/` not yet vendored.

**Files Modified:** `bootstrap.sh`, `lib/common.sh`, `lib/cmd-doctor.sh`.

**Ceremony:** Small epic (3 stories). Epic smoke: `bash -n` passes on all 3 files;
`./bootstrap.sh doctor` exit 0; `./bootstrap.sh` usage exit 0. Full bats suite deferred to E5.
