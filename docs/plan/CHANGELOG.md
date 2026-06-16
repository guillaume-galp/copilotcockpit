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
