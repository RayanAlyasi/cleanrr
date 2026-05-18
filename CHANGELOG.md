# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
