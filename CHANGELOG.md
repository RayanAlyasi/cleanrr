# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.1.0...v0.1.1) (2026-05-18)


### Bug Fixes

* **ci:** skip codecov upload when CODECOV_TOKEN is unset ([#13](https://github.com/RayanAlyasi/cleanrr/issues/13)) ([297b951](https://github.com/RayanAlyasi/cleanrr/commit/297b95120aa8e3e985d9710242d49627fcbf3f02))
* **deps:** bump trivy-action to v0.36.0 ([#10](https://github.com/RayanAlyasi/cleanrr/issues/10)) ([7f23e06](https://github.com/RayanAlyasi/cleanrr/commit/7f23e06eb917ea54a2744de26ec55324a1966961))

## [Unreleased]

### Added
- Project scaffold: Dockerfile, docker-compose.yml, pydantic-settings configuration.
- Telegram bot with `/start`, `/help`, and free-text message handlers.
- Claude Agent SDK integration via `ClaudeSDKClient`, with per-user session isolation.
- Dual authentication: Claude.ai subscription OAuth token or Anthropic API key.
- Configurable model and system prompt via environment variables.
- MIT license, README, contributing guide.
- CI workflow: ruff (lint + format), pyright (types), pytest (tests).
- Pre-commit hooks for ruff and basic file hygiene.
