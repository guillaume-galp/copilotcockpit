---
name: "TH1-E4: CI/CD & releases"
about: "Stories for CI/CD & releases — VERSION/CHANGELOG, release.yml, ci.yml"
labels: ["TH1", "E4"]
---

## Epic: TH1-E4 — CI/CD & releases

The delivery machinery for `copilotcockpit` itself: semver source of truth, the
tag-driven release pipeline, and the PR-checks workflow.

### Stories

- [ ] US1 — `VERSION` + `CHANGELOG.md`: single semver source of truth + agent-maintained notes.
- [ ] US2 — `.github/workflows/release.yml`: tag-driven tarball + sha256 + `gh release --latest` + cat-5 validation.
- [ ] US3 — `.github/workflows/ci.yml`: PR checks running test categories 1–4.

Full stories: `docs/themes/TH1-bootstrap-tooling/E4-cicd-releases/`
