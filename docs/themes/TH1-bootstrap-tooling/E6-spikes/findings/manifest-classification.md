# Findings — MANIFEST.toml glob classification matcher

> **Spike:** `TH1-E6-US2` — MANIFEST.toml glob classification correctness
> **Purpose:** Prove a bash technique that classifies every scaffolded `e2e/` path as
> `framework` / `seed` / `project` from the `MANIFEST.toml` globs, so that
> `e2e --update` provably refreshes the right files and never clobbers project-owned
> content (NFR-5). Gates **E3-US4** (`MANIFEST.toml`) and **E3-US6** (`--update`).
> Reference: [ADR-006](../../../../ADRs/ADR-006-template-update-strategy.md),
> architecture §7 (`MANIFEST.toml`) & §11.

This note was produced by **actually running** the candidate matcher against a
throwaway fixture in this GNU/Linux environment. Bash:
`GNU bash, version 5.3.9(1)-release (x86_64-pc-linux-gnu)`.

---

## 1. Chosen mechanism (AC5)

**Bash `[[ "$path" == $GLOB ]]` pattern matching, driven by literal arrays per
ownership class, with `shopt -s globstar extglob` enabled.**

- `[[ str == glob ]]` is *string* matching (not pathname expansion), so it classifies
  a path **without** needing the file to exist and without spawning `find`. This makes
  the matcher pure-bash, deterministic, and trivially unit-testable.
- The manifest patterns are stored as three arrays (`FRAMEWORK`, `SEED`, `PROJECT`)
  mirroring the `[framework]`/`[seed]`/`[project]` tables in architecture §7.

### Portability caveat (from E6-US1 — `portability-cheatsheet.md`)

The E6-US1 cheat-sheet warns that **macOS system bash is 3.2** (no associative arrays,
no `mapfile`, **and `globstar` was only added in bash 4.0**), and that `[[`, `shopt`,
`extglob`, `globstar` are **bash-only** (POSIX `sh` lacks all of them — verified:
`sh -c 'shopt …'` → `shopt: not found`).

**Empirically tested nuance that de-risks this:** inside `[[ str == glob ]]` a *single*
`*` already matches `/`, so `tests/*`, `runs/*`, `.github/skills/*` match nested paths
**with `globstar` turned off**:

```text
$ shopt -u globstar
.github/skills/worker-dev/SKILL.md -> matched by single-star
```

➡️ **Conclusion:** the `[[ … == … ]]` matcher does **not** functionally depend on
`globstar` for the `**` patterns — the `**` in the manifest is documentary, and a lone
`*` matches across `/` in `[[ ]]` regardless. The matcher therefore runs correctly on
**bash 3.2 (macOS) and bash 5 (Linux)** alike. We still `shopt -s globstar extglob`
defensively (and to support `extglob` should future manifest patterns need it), but
ship targeting bash 3.2 features only per the E6-US1 doctrine. `globstar` *is* required
only if a future implementation switches to real pathname expansion (`for f in tests/**`)
— **do not** rely on that on macOS without `brew install bash`.

---

## 2. Classification precedence (most-specific / project-first-wins)

A single path can match patterns in more than one class (e.g. `tests/smoke.spec.ts`
matches the seed literal `tests/smoke.spec.ts` **and** the project glob `tests/**`).
The matcher resolves ambiguity by **specificity, then by the project-safe default**:

1. **Literal `seed` entries** (most specific — exact paths) — checked first.
2. **Literal `framework` entries** (exact paths).
3. **`project` globs** (wildcards — least specific).
4. **Default `project`** — anything unmatched is *never touched* (the safe failure
   mode, ADR-006 *Risks*).

Rationale: literal exact paths are more specific than wildcard globs, so a seed file
that also falls under a broad project glob is correctly seeded once (created-if-missing)
rather than treated as a wildcard project file. Where a tie or a gap exists, **`project`
(never-touch) wins** — overwriting project content is unrecoverable, whereas a missed
refresh is merely a no-op a developer can re-run.

---

## 3. The matcher snippet (bash)

```bash
#!/usr/bin/env bash
# MANIFEST.toml ownership classifier (ADR-006). Pure-bash, no I/O, no find.
set -u
shopt -s globstar extglob   # defensive; the [[ == ]] matcher works on bash 3.2 too

FRAMEWORK=( "run-audit.sh" "run-playwright.sh" "playwright.config.ts"
  "global-setup.ts" "global-teardown.ts" "governance/GOVERNANCE.md"
  "governance/run-schema.yaml" "test-book/TC-FORMAT.md"
  ".github/copilot-instructions.md" )
SEED=( "package.json" ".env.example" ".gitignore"
  "test-book/CH01-smoke.md" "tests/smoke.spec.ts" "tests/helpers.ts" )
PROJECT=( "tests/**" "test-book/CH0[2-9]*.md" "test-book/CH1*.md"
  "test-book/SUMMARY.md" "runs/**" "tmux-cockpit.sh" "tmux-cockpit-local.sh"
  ".github/skills/**" ".env.local" ".env.*" )

classify() {
  local p="$1" g
  for g in "${SEED[@]}";      do [[ "$p" == $g ]] && { echo seed;      return; }; done
  for g in "${FRAMEWORK[@]}"; do [[ "$p" == $g ]] && { echo framework; return; }; done
  for g in "${PROJECT[@]}";   do [[ "$p" == $g ]] && { echo project;   return; }; done
  echo project   # AC4: unclassified -> safe default (never touch)
}
```

---

## 4. Result table (AC2 / AC3 — real output)

Produced by running the matcher above against the throwaway fixture tree. Each row is
the **actual** printed result, not a prediction.

| Fixture path | Resolved class | Pattern that matched | Expected (ADR-006) | ✓ |
|--------------|----------------|----------------------|--------------------|---|
| `tests/a.spec.ts` | `project` | `tests/**` | project | ✅ |
| `test-book/CH01-smoke.md` | `seed` | `test-book/CH01-smoke.md` (seed literal) | seed | ✅ |
| `test-book/CH02-foo.md` | `project` | `test-book/CH0[2-9]*.md` | project | ✅ |
| `runs/RUN-x.yaml` | `project` | `runs/**` | project | ✅ |
| `run-audit.sh` | `framework` | `run-audit.sh` (framework literal) | framework | ✅ |
| `package.json` | `seed` | `package.json` (seed literal) | seed | ✅ |
| `.env.local` | `project` | `.env.local` / `.env.*` | project | ✅ |
| `.github/skills/worker-dev/SKILL.md` | `project` | `.github/skills/**` | project | ✅ |
| `tests/smoke.spec.ts` | `seed` | `tests/smoke.spec.ts` (seed literal, beats `tests/**`) | seed | ✅ |
| `tests/helpers.ts` | `seed` | `tests/helpers.ts` (seed literal, beats `tests/**`) | seed | ✅ |
| `foo/extra.txt` | `project` | *(none — default)* | project (safe default) | ✅ |

All eleven paths resolved exactly as ADR-006 requires.

### AC3 confirmation — ADR-006 globs resolve correctly

- `tests/**`, `test-book/CH0[2-9]*.md`, `test-book/CH1*.md`, `runs/**`,
  `.github/skills/**`, `.env.*` → **`project`** ✅
- `test-book/CH01-smoke.md` + smoke spec (`tests/smoke.spec.ts`) + helpers
  (`tests/helpers.ts`) → **`seed`** ✅ (note `CH1*` matches `CH10…`/`CH11…` but **not**
  `CH01`, and `CH0[2-9]*` excludes `CH01`, so the smoke chapter correctly falls through
  to its seed literal)
- `run-audit.sh` (and the other framework literals) → **`framework`** ✅

---

## 5. Unclassified-path fallback (AC4)

A path present in **no** manifest class must be handled safely. The matcher's final
`echo project` makes the default **`project` → never touch** (ADR-006 *Risks*: the safe
failure mode). Demonstrated with the unlisted fixture file:

```text
foo/extra.txt                                project
```

`foo/extra.txt` matches no framework, seed, or project pattern, yet resolves to
`project`, so `--update` would leave it untouched. This guarantees the worst case of a
forgotten manifest entry is a **missed refresh**, never **data loss**. (Complementary
control per ADR-006: `doctor`/CI should additionally flag template paths that are in no
class, so the gap is caught at build time rather than silently defaulted.)

---

## 6. BDD scenario outcomes

| Scenario (from story) | Result |
|-----------------------|--------|
| A project file is never classified as framework (`tests/a.spec.ts`, `runs/RUN-x.yaml` → project) | ✅ both → `project` |
| A path absent from the manifest is treated safely (`foo/extra.txt` → project) | ✅ → `project` (safe default) |
| The smoke chapter is a seed, not framework (`test-book/CH01-smoke.md` → seed) | ✅ → `seed` |

---

## 7. Recommendation for E3-US4 / E3-US6

- Adopt the `[[ "$p" == $glob ]]` array-driven classifier above as the foundation of
  the `MANIFEST.toml` parser (E3-US4) and `--update` refresh engine (E3-US6).
- Keep the **seed → framework → project-globs → default-project** precedence order.
- Parse the three TOML `paths = [ … ]` arrays into the `SEED`/`FRAMEWORK`/`PROJECT`
  bash arrays at load time (a small `awk`/`sed` TOML reader, or `python3 -c` per the
  E6-US1 portability doctrine, is sufficient — the file is a flat fixed shape).
- Target bash 3.2: the matcher needs only `[[ == glob ]]`, **not** `globstar`, so it is
  macOS-safe. Add a `tests/template/check-template.sh` assertion that every template
  path classifies to exactly one class.

> **Throwaway fixture note:** the `e2e/` fixture tree and `classify.sh` used to produce
> these results were created under `/home/guillaume/cc-spike-us2` (outside the repo) and
> are **not committed** per the story's time-box. Reproduce by recreating the fixture
> files listed in the table and running the snippet in §3.
