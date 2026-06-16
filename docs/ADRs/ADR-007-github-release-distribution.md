# ADR-007 — GitHub release tarball distribution (cold install)

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`bootstrap.sh global` ([ADR-003](ADR-003-bootstrap-entry-point.md)) installs the seven
skills + `cockpit-wake` into the user's home. ADR-001 and ADR-005 assume the install
source is a **local clone** of this repo. But the most important onboarding path is the
**cold install** — a new machine or a new team member who does *not* have a clone and
wants the harness with a single command (VP1 §6 step 1, NFR-7, NFR-8).

The cold-install source must be:
- **Versioned** — a specific, reproducible release, not a moving `main`;
- **Deterministic** — same input → same bytes (so installs are auditable);
- **Lightweight** — it should not require cloning the whole repo (history, docs, CI);
- **Self-sufficient** — it must carry enough to run *both* `global` *and* `e2e`
  (a developer who cold-installs will want to scaffold projects too).

Candidate mechanisms for "pull from GitHub when no local clone is present":
(a) `curl` a tagged tarball from **GitHub Releases**; (b) `git clone --depth=1`,
install, then delete; (c) individual `curl` calls per file to `raw.githubusercontent.com`.

## Decision

**`bootstrap.sh global` cold-installs from a versioned GitHub Releases tarball (option a).**

### The install surface tarball

CI publishes, per release tag, a single asset:

```
copilotcockpit-v<M.m.f>.tar.gz       # e.g. copilotcockpit-v1.2.3.tar.gz
copilotcockpit-v<M.m.f>.tar.gz.sha256
```

The tarball bundles **exactly the install surface** — everything needed to run both
bootstrap phases, and nothing else (no `docs/`, no `.git/`, no tests):

```
copilotcockpit/
├── bootstrap.sh
├── lib/
├── skills/            # all 7 SKILL.md files
├── bin/cockpit-wake
├── templates/e2e/     # so `bootstrap.sh e2e` works from the tarball too
└── README.md
```

(How CI assembles and publishes this is specified in architecture §8.)

### The documented one-liner

```bash
curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/copilotcockpit.tar.gz | tar -xz
./copilotcockpit/bootstrap.sh global
```

The release also ships a tiny `install.sh` wrapper (curl-extract-run) so the even-shorter
form works:

```bash
bash <(curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/install.sh)
```

### Version selection inside the bootstrap

`bootstrap.sh global` gains a `--from-release <ref>` flag:

- `--from-release latest` (default for cold install) → resolve the latest release via the
  GitHub API (`/repos/<org>/copilotcockpit/releases/latest`), then `curl` its tarball asset.
- `--from-release v1.2.3` → pin to an exact tag's tarball asset.
- When run **inside a local clone** with no `--from-release`, the script uses the local
  files (the ADR-001 path) and never touches the network — cold-install is the *fallback*,
  not the default, for users who already have the repo.

### Integrity

After download, the bootstrap fetches the matching `.sha256` and **verifies** the tarball
before extracting; a mismatch aborts the install. This makes the cold path tamper-evident
and deterministic.

## Consequences

### Positive
- True one-command cold install on a fresh machine, no `git` required for the download
  (only `curl` + `tar`) — strengthens NFR-3 (minimal deps) and NFR-8 (discoverable).
- **Versioned & deterministic**: every install pins to a release tag; the `.sha256`
  makes the bytes auditable and tamper-evident.
- **Small**: the tarball carries only the install surface (~tens of KB), not repo history.
- The same artefact powers both `global` and `e2e`, so a cold-installed user is fully
  operational.
- `latest` keeps the one-liner stable across releases; `--from-release vX.Y.Z` allows pinning.

### Negative / Trade-offs
- Requires a **release pipeline** to exist and publish the asset on every tag (built in
  architecture §8). Until a release exists, only the local-clone path works.
- The install surface is **duplicated** between the repo layout and the tarball; CI must
  assemble it correctly. Mitigation: a single CI step (architecture §8) with an explicit
  file list; a smoke job can extract-and-run `global --dry-run` to prove the tarball works.
- Resolving `latest` via the GitHub API can hit unauthenticated rate limits in CI-heavy
  environments. Mitigation: the documented one-liner uses the
  `releases/latest/download/<asset>` redirect (no API call); the API is only used by
  `--from-release latest` from inside the script, and honours `GH_TOKEN` if present.

### Risks
- A user behind a proxy that blocks GitHub Releases CDN. Mitigation: the local-clone path
  (ADR-001) remains fully supported and offline-capable; cold install is additive.
- Asset-name drift (`copilotcockpit.tar.gz` vs `copilotcockpit-v1.2.3.tar.gz`). Mitigation:
  CI publishes **both** — a version-stamped asset *and* a stable unversioned alias name via
  the `releases/latest/download/` redirect — and the README pins one canonical one-liner.

## Alternatives Considered

### (b) `git clone --depth=1`, install, then delete
- Pros: reuses the ADR-001 local-clone code path; no separate artefact to build.
- Cons: requires `git`; downloads the entire tree (docs, CI, templates we may not need at
  that moment); leaves the user without a persistent clone unless they keep it; "clone then
  rm -rf" is an awkward, surprising UX for a one-liner.
- **Rejected** as the cold-install default — heavier and less deterministic than a pinned
  tarball — though a user may of course clone manually and use the local path.

### (c) Per-file `curl` to `raw.githubusercontent.com`
- Pros: no release artefact needed; pulls only the files touched.
- Cons: **not atomic or versioned** (raw URLs track a branch/SHA, not a release); N HTTP
  requests (one per skill + binary + lib file) is slow and fragile; no integrity check; a
  partial failure leaves a half-installed state — violating NFR-1 (idempotent/atomic) and
  NFR-7 (fast).
- **Rejected** — fails determinism, atomicity, and integrity goals.

## History
- 2026-06-16: Proposed
