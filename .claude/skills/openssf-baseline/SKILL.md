---
name: openssf-baseline
description: How each OpenSSF Security Baseline maturity-1 and maturity-2 requirement is satisfied in cleanrr, and which diff would regress it. Preloaded into cleanrr-security; consulted by /cleanrr-audit.
user-invocable: false
---

# OpenSSF Security Baseline — cleanrr mapping

Source: `ossf/security-baseline` `baseline/OSPS-*.yaml` (fetched 2026-09-15). Only maturity-1 and maturity-2 requirements are listed; cleanrr holds both badges and must not regress either. Controls marked **out-of-band** live in GitHub settings, not the repository; a diff cannot regress them, so do not report on them unless a workflow or doc change implies they are being bypassed.

| Control | Requirement (short) | Satisfied by | A regressing diff looks like |
| --- | --- | --- | --- |
| AC-01.01 | MFA for sensitive repo actions | GitHub account setting | out-of-band |
| AC-02.01 | Least-privilege default for new collaborators | GitHub default | out-of-band |
| AC-03.01 | Direct commits to `main` blocked | Ruleset 16513676 "main protection", zero bypass | out-of-band |
| AC-03.02 | Primary branch deletion protected | Same ruleset | out-of-band |
| AC-04.01 (L2) | CI jobs default to least privilege | Top-level `permissions: contents: read` in every workflow; job-level `write` only where needed | A workflow without a top-level `permissions:` block, or a top-level `write` |
| BR-01.01 | Untrusted metadata sanitized in CI | No `${{ github.event.* }}` or `${{ inputs.* }}` interpolated inside `run:` scripts; values go through `env:` | An expression from an issue/PR title, branch name, or input pasted into `run:` |
| BR-01.03 | Untrusted code can't reach privileged credentials | No `pull_request_target`; PR CI has read-only token | A `pull_request_target` trigger that checks out the PR head, or a secret exposed to a PR job |
| BR-02.01 (L2) | Unique version per release | release-please + `hatch-vcs` tags | Hand edits to `.release-please-manifest.json`; a tag that isn't PEP 440 |
| BR-03.01 / 03.02 | Official channels and distribution over HTTPS with MITM protection | README links, GHCR over TLS | An `http://` link to a project channel or download |
| BR-04.01 (L2) | Release contains a change log | release-please generates `CHANGELOG.md` from Conventional Commits | Hand edits to `CHANGELOG.md`; a user-visible change committed as `chore:` |
| BR-05.01 (L2) | Standard dependency tooling | `pyproject.toml` ranges + `constraints.txt` exact pins + Dependabot | A vendored copy of a library, or a `constraints.txt` pin that drifts from `pyproject.toml` (guarded by `tests/test_consistency.py`) |
| BR-06.01 (L2) | Releases signed | `release.yml` cosign keyless step, `id-token: write` on that job only | Removing the cosign step, or dropping `id-token: write` |
| BR-07.01 | No secrets in VCS | gitleaks in CI and pre-commit; `.env` gitignored; `.env.example` has empty values | A token-shaped string in any file; `.env` un-ignored; a real key in `.env.example` |
| DO-01.01 | User guide for all basic functionality | README "What it does today", "Commands", "Configuration" | A new command, tool, or Settings field without its README row |
| DO-02.01 | Defect reporting guide | CONTRIBUTING "Reporting bugs" | Section removed |
| DO-06.01 (L2) | Dependency selection and tracking described | CONTRIBUTING "Dependency management"; `dependabot.yml` with `cooldown` | Removing the cooldown; adding an ecosystem without documenting it |
| DO-07.01 (L2) | Build instructions | README "Quick start", CONTRIBUTING "Development setup", `Dockerfile`, `constraints.txt` | A new build step or required env var not documented |
| GV-01.01 / 01.02 (L2) | Members with sensitive access, and their roles | `MAINTAINERS.md` | A maintainer change without updating the file |
| GV-02.01 | Public discussion mechanism | GitHub issues enabled | out-of-band |
| GV-03.01 / 03.02 (L2) | Contribution process and contributor guide | `CONTRIBUTING.md` | Quality bar or PR steps removed or made inaccurate |
| LE-01.01 (L2) | Every commit asserts legal authorization | DCO check (`dco.yml`, required status), `git commit -s`, release-please configured to sign off | A commit without `Signed-off-by`; `dco.yml` removed or made non-required |
| LE-02.01 / 02.02 | OSI license for source and releases | MIT | License changed to a non-OSI text |
| LE-03.01 / 03.02 | License file in repo and in released assets | `LICENSE`; `Dockerfile` copies `LICENSE` into the image | `COPY pyproject.toml LICENSE README.md` losing `LICENSE` |
| QA-01.01 / 01.02 | Public repo with full history | GitHub | out-of-band |
| QA-02.01 | Direct dependency list | `[project.dependencies]` with version bounds | An import with no entry in `pyproject.toml`; an unbounded `>=` only |
| QA-03.01 (L2) | Status checks must pass before merge | Rulesets require the CI job names | **Renaming a CI job or workflow** without updating the ruleset leaves a required check that never reports |
| QA-05.01 / 05.02 | No generated executables or unreviewable binaries | `.gitignore`; only `assets/` images are binary | A `.pyc`, `.whl`, `.so`, `.exe`, archive, or model file added; a new binary outside `assets/` |
| QA-06.01 (L2) | Automated tests run before merge | `ci.yml` `check` job runs pytest on every PR | pytest step removed or made non-blocking; tests skipped by marker |
| SA-01.01 (L2) | Design doc with all actors and actions | `ARCHITECTURE.md` "Actors" | A new command, tool, or role without an Actors update |
| SA-02.01 (L2) | External interfaces documented | `ARCHITECTURE.md` "External interfaces" table | A new outbound client, endpoint family, or inbound listener without a table row |
| SA-03.01 (L2) | Security assessment | `THREAT_MODEL.md` | A new destructive tool, trust boundary, or credential without a threat entry or an update to an existing one |
| VM-01.01 (L2) | CVD policy with response timeframe | `SECURITY.md` (7-day acknowledgment) | Timeframe removed |
| VM-02.01 | Security contacts | `SECURITY.md` | Contact path removed |
| VM-03.01 (L2) | Private vulnerability reporting | GitHub private vulnerability reporting enabled; `SECURITY.md` points to it | Doc pointing to public issues instead |
| VM-04.01 (L2) | Vulnerability data published | GitHub advisories + `CHANGELOG.md` `fix:` entries | Security fix committed as `chore:` so it never appears in the changelog |

## Scorecard hygiene the badges also depend on

- Every `uses:` in `.github/workflows/` is pinned to a 40-hex commit SHA with a version comment. A tag or branch reference is a finding.
- `Dockerfile` `FROM` keeps both the tag and the `@sha256:` digest (Dependabot needs the tag to compute bumps).
- `dependabot.yml` keeps `cooldown.default-days` on every ecosystem.
- `release.yml` keeps the Trivy scan with `exit-code: "1"` on CRITICAL/HIGH and the pip/setuptools strip in `Dockerfile` that made it pass; `docker.yml` runs the same gate (`exit-code: "1"`, CRITICAL/HIGH, `ignore-unfixed`) on every pull request, and the two must not diverge.
- `semgrep ci` keeps `--no-suppress-errors`; without it a failed ruleset fetch passes green.
- `ruff`, `pyright` (strict), and `bandit -ll` stay blocking in `ci.yml`, their versions pinned in `constraints.txt` and installed with `-c`. New functionality lands with tests in the same change (badge criterion `tests_are_added`).
- Random values for link codes and confirmation ids come from `secrets`, never `random` (badge criterion `crypto_random`).

## How to report

Under `## Baseline`, one line per affected control: `OSPS-XX-NN.NN — holds | regressed | needs update — evidence`. Only list controls the change can affect. A regressed maturity-2 control is at least High; a regressed maturity-1 control is Critical.
