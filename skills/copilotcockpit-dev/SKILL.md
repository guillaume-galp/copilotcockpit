---
name: copilotcockpit-dev
description: "GitOps delivery runbook for agents developing copilotcockpit itself. The canonical, in-repo source of truth for taking any change from idea to a published release, with a test gate at every step and bounded escalation. USE FOR: branching/committing on this repo, running the local test gate, opening and merging PRs, deriving the semver bump from the commit type, bumping VERSION + CHANGELOG.md, tagging, and confirming the tag-driven release."
---

# copilotcockpit-dev — GitOps Delivery Runbook

You are an agent **developing `copilotcockpit` itself** (the bash + Python +
templates toolkit that is the canonical source of truth for every project's E2E
harness). This skill is the **canonical, versioned runbook** for delivering any
change to this repo — from idea to a published release — following GitOps best
practice with a clear test gate at every step and a bounded escalation path.

It is the **8th repo-managed skill**, installed to
`~/.copilot/skills/copilotcockpit-dev/SKILL.md` by `bootstrap.sh global`.

> Authoritative spec: **ADR-008** (this skill mirrors it faithfully); see also
> architecture §9 (dev workflow / test strategy) and ADR-007 (tag → release).
> This ADR owns **idea → tag**; ADR-007 owns **tag → release**.

---

## 1. Branch & commit conventions

- Branch from **latest `main`** (`git pull --ff-only origin main` first), naming
  the branch `feature/<slug>` for a new capability or `fix/<slug>` for a bug fix.
- **Never commit directly to `main`** — the *only* exception is the version-bump
  commit in Phase 3 (step 10).
- All commits use **[Conventional Commits](https://www.conventionalcommits.org/)**.
  Allowed types: `feat`, `fix`, `perf`, `refactor`, `docs`, `chore`, `test`,
  `ci`, with an optional `!` and/or a `BREAKING CHANGE:` footer.
- **Validate the commit message *before* pushing.** A message that does not match
  `^(feat|fix|perf|refactor|docs|chore|test|ci)(\(.+\))?!?: .+` is a hard stop —
  fix the message, do not push.

---

## 2. Conventional commit → version bump mapping

The squashed PR commit's type determines the semver bump (Phase 3):

| Commit type | Version bump | Releases? |
|-------------|--------------|-----------|
| `feat:` | **minor** (`0.X.0`) | yes |
| `fix:` / `perf:` | **patch** (`0.0.X`) | yes |
| `feat!:` / any `BREAKING CHANGE:` footer | **major** (`X.0.0`) | yes |
| `chore:` / `docs:` / `refactor:` / `test:` / `ci:` (alone) | **none** | no — skip Phases 3–4 |

If the bump is **none**, stop after merge (Phase 2): there is no release.

---

## 3. Test taxonomy — the gate

Five categories, dispatched by `./run-tests.sh <category>`:

| # | Category | Tool | Scope | When | Gate |
|---|----------|------|-------|------|------|
| 1 | **Script unit** | `bats` (`tests/unit/cmd-*.bats`) | each `lib/cmd-*.sh`: idempotency (run twice = same), dry-run = no side-effects, error paths exit with correct code+message | local + CI-on-PR | must pass |
| 2 | **Template integrity** | `tests/template/check-template.sh` | `*.tmpl` have no unresolved tokens after substitution; `package.json.tmpl` is valid JSON; `MANIFEST.toml` accounts for every file in `templates/e2e/`; `run-audit.sh` & `run-playwright.sh` pass `bash -n` | CI-on-PR | must pass |
| 3 | **Skills lint** | `tests/skills/lint-skills.sh` (+ `python3 -c "import yaml"`) | every `skills/*/SKILL.md` is valid Markdown with parseable frontmatter; `name:`/`description:` non-empty | CI-on-PR | must pass |
| 4 | **Integration smoke** | `bats` (`tests/integration/smoke.bats`) | `global --dry-run` enumerates all 8 skills + cockpit-wake; `e2e <tmp> --dry-run` prints the scaffold file list; `doctor` exits 0 | CI-on-PR + post-release | must pass |
| 5 | **Release asset validation** | post-publish CI job (`release.yml`) | download the published tarball, verify SHA-256, extract, run `global --dry-run` + `doctor` from the extracted dir | CI post-release only | must pass (else fail the release) |

**Local pre-commit gate = categories 1–4:**

```bash
./run-tests.sh unit && \
./run-tests.sh template && \
./run-tests.sh skills && \
./run-tests.sh integration
# or, equivalently:
./run-tests.sh all
```

Category 5 runs **only** in the release workflow — never locally.

> `bats` and PyYAML are **contributor-only** dependencies (categories 1, 3, 4).
> End-user install (`bootstrap.sh global`/`e2e`) stays dependency-light.

---

## 4. The 12-step flow

### Phase 1 — Feature/fix delivery on a branch

1. **Branch.** `git pull --ff-only origin main`; create `feature/<slug>` or
   `fix/<slug>` from latest `main`.
2. **Implement, then run the local gate** (categories 1–4):
   ```bash
   ./run-tests.sh unit && ./run-tests.sh template && \
   ./run-tests.sh skills && ./run-tests.sh integration
   ```
3. **All green → commit + push + open PR.** Use a validated Conventional Commit
   message, then:
   ```bash
   git push -u origin <branch>
   gh pr create --base main --fill   # title MUST follow Conventional Commits
   ```
   State in the PR body which release this triggers, e.g.
   *"Merging this (a `feat:`) will trigger release v0.2.0."*
4. **Monitor CI** until every check passes:
   ```bash
   gh pr checks --watch
   gh run watch
   ```
5. **On CI failure**, read the log, fix, re-push:
   ```bash
   gh run view --log-failed
   ```
   **Max 3 auto-fix attempts** at this gate, then escalate (see §5).

### Phase 2 — Merge gating

6. A PR may merge **only** when: (a) all CI jobs are green, (b) there are no merge
   conflicts, and (c) the PR title follows Conventional Commits.
7. **Merge with squash + auto:**
   ```bash
   gh pr merge --squash --auto
   ```
   **Never force-push to `main`; never merge with failing checks.** `--auto`
   still respects branch-protection required reviews.

   > If the bump for the squashed commit type is **none** (§2), **stop here** —
   > there is no version bump and no release.

### Phase 3 — Version bump + tag

8. **Sync and derive the bump:**
   ```bash
   git pull --ff-only origin main
   ```
   Derive the bump type from the squashed commit type (§2 table). If **none** →
   stop.
9. **Update `VERSION` and `CHANGELOG.md`.** Write the new plain semver string
   (e.g. `0.2.0`, no leading `v`) into `VERSION`, and **prepend** a
   `## v0.2.0 — YYYY-MM-DD` section to `CHANGELOG.md` (newest first), populated
   from the squash-commit body.
10. **Commit the bump directly to `main`** (the *only* allowed direct-to-`main`
    commit) and push:
    ```bash
    git commit -am "chore: bump version to v0.2.0 [skip ci]"
    git push origin main
    ```
    The `[skip ci]` marker stops `ci.yml` from re-running on this push (it only
    touches `VERSION`/`CHANGELOG.md`).
11. **Tag and push the tag:**
    ```bash
    git tag -a v0.2.0 -m "Release v0.2.0"
    git push origin v0.2.0
    ```

### Phase 4 — Release confirmation

12. The pushed `vX.Y.Z` tag triggers **`release.yml`** (ADR-007), which builds the
    install tarball, computes its `.sha256`, and publishes the GitHub Release.
    Monitor and confirm:
    ```bash
    gh run watch
    gh release view v0.2.0
    ```
    Confirm the published assets exist —
    `copilotcockpit-v0.2.0.tar.gz` + `.sha256` **and** the stable-alias
    `copilotcockpit.tar.gz` + `.sha256` (served at
    `releases/latest/download/`) — and that **Category-5** release-asset
    validation is green. If Category 5 fails, the release fails; escalate (§5).

---

## 5. Bounded-autonomy escalation policy

**Autonomy is bounded — no silent loops.** At *any* failing CI/test gate, make at
most **3 auto-fix attempts**. After the third failed attempt:

1. **Stop.** Do not push a 4th fix.
2. **Summarise** the failure: which gate, the error, and each of the 3 things you
   tried (and why each did not work).
3. **Escalate to the human**: post the summary as a PR comment and a cockpit
   message, and wait for direction.

An agent that loops forever fixing CI is worse than one that escalates early.

---

## 6. Single source of semver truth

- The **`VERSION`** file (a plain semver string, e.g. `0.2.0`) is the **single
  source of truth** for the version. There is no `package.json` version field to
  keep in sync.
- `VERSION` and the release **tag** are kept equal by steps 9–11.
- **`CHANGELOG.md`** is the human-readable record: new `## vX.Y.Z — YYYY-MM-DD`
  sections are **prepended** (newest first) from the squash-commit body in
  step 9. On release, `release.yml` **sources the matching section into the
  GitHub Release body** — so an accurate, well-formed CHANGELOG entry is what
  becomes the published release notes.

---

## Quick reference — the `gh` commands

| Step | Command |
|------|---------|
| Open PR | `gh pr create --base main --fill` |
| Watch checks | `gh pr checks --watch` |
| Watch a run | `gh run watch` |
| Read failed logs | `gh run view --log-failed` |
| Merge | `gh pr merge --squash --auto` |
| Confirm release | `gh release view vX.Y.Z` |
