# ADR-007 — GitHub release tarball distribution (cold install)

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`bootstrap.sh global` ([ADR-003](ADR-003-bootstrap-entry-point.md)) installs the
managed GitHub Copilot skills and `cockpit-wake` into the user's home.
`bootstrap.sh codex-global` installs the same managed skills for Codex. ADR-001
and ADR-005 assume the install source is a local clone of this repo. The cold
install path supports a new machine or team member without a clone.

The cold-install source must be:
- **Versioned** — a specific, reproducible release, not a moving `main`;
- **Deterministic** — same input → same bytes (so installs are auditable);
- **Lightweight** — it should not require cloning the whole repo (history, docs, CI);
- **Self-sufficient** — it must carry enough to run `global`, `codex-global`,
  `e2e`, and `uninstall.sh`.

Candidate mechanisms for "pull from GitHub when no local clone is present":
(a) `curl` a tagged tarball from **GitHub Releases**; (b) `git clone --depth=1`,
install, then delete; (c) individual `curl` calls per file to `raw.githubusercontent.com`.

## Decision

**Cold install uses a versioned GitHub Releases tarball (option a).**

### The install surface tarball

CI publishes, per release tag, versioned tarball assets:

```
copilotcockpit-v<M.m.f>.tar.gz       # e.g. copilotcockpit-v1.2.3.tar.gz
copilotcockpit-v<M.m.f>.tar.gz.sha256
```

The tarball bundles exactly the install surface and nothing else (no `docs/`,
no `.git/`, no tests):

```
copilotcockpit/
├── bootstrap.sh
├── uninstall.sh
├── lib/
├── skills/            # all managed SKILL.md files
├── bin/cockpit-wake
├── templates/e2e/     # so `bootstrap.sh e2e` works from the tarball too
└── README.md
```

(How CI assembles and publishes this is specified in architecture §8.)

### The documented one-liner

```bash
curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/copilotcockpit.tar.gz | tar -xz
./copilotcockpit/bootstrap.sh global
./copilotcockpit/bootstrap.sh codex-global
```

The release also ships `install.sh` and `uninstall.sh` assets. `install.sh`
installs both Copilot and Codex user-scoped skills. `uninstall.sh` removes the
managed user-scoped install.

```bash
bash <(curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/install.sh)
curl -fsSLO https://github.com/<org>/copilotcockpit/releases/latest/download/uninstall.sh
bash uninstall.sh
```

### Version selection inside the bootstrap

`bootstrap.sh global` supports a `--from-release <ref>` flag:

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
- The same artefact powers `global`, `codex-global`, `e2e`, and uninstall.
- `latest` keeps the one-liner stable across releases; `--from-release vX.Y.Z` allows pinning.

### Negative / Trade-offs
- Requires a **release pipeline** to exist and publish the asset on every tag (built in
  architecture §8). Until a release exists, only the local-clone path works.
- The install surface is **duplicated** between the repo layout and the tarball; CI must
  assemble it correctly. Mitigation: a single CI step (architecture §8) with an explicit
  file list; a smoke job can extract and run `global --dry-run`,
  `codex-global --dry-run`, and `uninstall.sh --dry-run`.
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
