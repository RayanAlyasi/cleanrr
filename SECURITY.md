# Security Policy

## Reporting a vulnerability

Please **don't** open a public issue for security vulnerabilities.

Use GitHub's private vulnerability reporting: go to the [Security tab](https://github.com/RayanAlyasi/cleanrr/security) and click "Report a vulnerability". This opens a private advisory visible only to the maintainer until it's triaged, so details don't leak before a fix is out.

**Response timeframe**: cleanrr has a single, solo maintainer — expect an initial acknowledgment within 7 days. Confirmed vulnerabilities are prioritized over other work; a fix timeline depends on severity and will be communicated in the advisory thread.

## Supported versions

cleanrr is a self-hosted single-instance bot with no LTS branches. Only the latest release is supported — please upgrade before reporting an issue that might already be fixed.

## Threat model

See [THREAT_MODEL.md](THREAT_MODEL.md) for the project's security assessment — the most likely and impactful risks, what's already mitigated, and what's explicitly accepted or out of scope.

## Secrets management

- **Storage**: all credentials (Telegram bot token, Anthropic auth, Overseerr/Sonarr/Radarr/qBittorrent API keys) live in a single `.env` file, excluded from version control by `.gitignore`. `.env.example` documents every variable's shape without real values.
- **Access**: every credential is a `pydantic.SecretStr` field in `cleanrr/config.py`, not a plain `str` — `.get_secret_value()` is required to use one, and none is ever put into a log line or a Telegram reply.
- **Detection**: gitleaks scans the full git history on every push and pull request, plus a local pre-commit hook, so an accidentally-committed secret is caught before it can merge.
- **Rotation**: each credential is independent (issued by a different service — @BotFather, Anthropic Console, each \*arr app's own Settings page, qBittorrent's WebUI), so rotating one doesn't require touching the others. Update the value in `.env` and restart the container. There's no automated rotation schedule or expiry reminder — rotation is on-demand (e.g. after a suspected leak), not calendar-driven.

## Vulnerability remediation policy

**Dependencies (SCA)**: [pip-audit](https://github.com/pypa/pip-audit) runs against the full resolved dependency tree on every push and pull request and is a required status check with no bypass except an explicit repository-owner admin override. Any known vulnerability blocks the build — the effective threshold is zero-day: it must be resolved (typically a version bump) or explicitly suppressed via pip-audit's `ignore-vulns` input with a documented reason before a PR can merge. The same gate runs ahead of every release build.

**Static analysis (SAST)**: [semgrep](https://semgrep.dev/) (Community ruleset) and [CodeQL](https://codeql.github.com/) both run on every push and pull request, and are both required status checks under the same zero-bypass-except-admin-override enforcement. A finding blocks merge; there's no accumulate-and-fix-later grace period. A finding judged non-exploitable is suppressed inline with a documented reason (`# nosemgrep: <reason>`, or a dismissed CodeQL alert with justification) rather than silently ignored.
