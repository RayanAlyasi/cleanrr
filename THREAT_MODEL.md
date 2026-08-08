# Threat model

This is an internal security assessment, not an external audit or pentest. It's based on direct review of the current codebase (`cleanrr/permissions/`, `cleanrr/identity.py`, `cleanrr/config.py`, `cleanrr/tools/*_write.py`, `cleanrr/handlers.py`) and the actual deployment shape (single Docker container on a homelab network, alongside Sonarr/Radarr/Overseerr/qBittorrent). It should be revisited whenever a new tool, a new external integration, or a change to the confirmation/identity model ships — not just on a calendar cadence.

See [README's Architecture section](README.md#architecture) for the actor list and external interface table this assessment assumes.

## Methodology

Threats below were identified by tracing three things through the actual code, not by generic checklist:

1. Every path from untrusted input (a Telegram message from anyone) to a state-mutating action.
2. Every place a credential is read, held, or could leak.
3. What happens if each trust boundary (Telegram user → bot, bot → Claude, bot → \*arr stack) is itself compromised.

## Threats

### 1. `.env` file compromise (highest impact)

**What**: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`, and the API keys for Overseerr, Sonarr, Radarr, and qBittorrent all live in one `.env` file, read into `pydantic.SecretStr` fields at startup. cleanrr typically runs on the same Docker host as the rest of a self-hosted media stack.

**Likelihood**: Low on its own — this isn't a network-exposed credential. But it's not cleanrr's boundary to defend; it depends entirely on the security of the host and every other container sharing it.

**Impact**: High. One compromised file yields the bot's Telegram identity, Claude API access (billing + potential misuse), and admin-level control over the entire \*arr stack, all at once. There's no credential-scoping between them.

**Existing mitigation**: Every credential field is `SecretStr`, not `str` — `.get_secret_value()` is required to use one, and nothing puts a raw token in a log line or a Telegram reply (`export_sdk_credentials`/`clear_sdk_credentials` in `config.py` even scope how long Claude's auth sits in `os.environ`). This bounds *accidental* leakage from cleanrr's own code. It does not, and cannot, defend against host-level or sibling-container compromise.

**Residual risk**: accepted. This is a general homelab deployment risk, not specific to cleanrr, and out of the project's control. Documented here so it's not silently assumed away.

### 2. Prompt injection via untrusted upstream data

**What**: Claude's context includes tool *results* — titles, torrent names, status text — sourced from Overseerr/Sonarr/Radarr/qBittorrent. Since a torrent name or media title is attacker-influenceable in principle (e.g. crafted to read like an instruction), this is a real prompt-injection surface.

**Likelihood**: Medium — requires an attacker with some ability to influence content that later flows through one of the \*arr integrations, or a compromised upstream service.

**Impact**: Bounded, by design, not by luck. Claude never has direct network access to the homelab — confirmed in code: only `cleanrr`'s own tool-layer functions execute HTTP calls (`cleanrr/tools/*.py`), Claude only ever sees tool *definitions* and *results*. A successful injection could get Claude to *request* a destructive tool call, but every state-mutating tool (`remove_my_request`, `delete_torrent`, `force_research_movie`, `force_research_show`) still routes through `can_use_tool` (`cleanrr/permissions/_callback.py`), which requires an explicit Telegram button tap from the specific user the action is scoped to, independent of whatever Claude "decided." Injected content can't tap that button.

**Existing mitigation**: the confirm/cancel gate plus the tool-layer's own ownership re-check (e.g. `remove_my_request` independently verifies `owner_id == caller_user_id` against Overseerr's own data, not just Claude's say-so — explicitly commented in code as defense-in-depth against "Overseerr's DELETE endpoint accepts any authenticated admin-API call").

**Residual risk**: low, accepted. The human-in-the-loop confirmation is the actual control here, not output filtering on Claude's responses (which doesn't exist and isn't the right layer for this).

### 3. Telegram account takeover of a linked user

**What**: If a linked user's own Telegram account is compromised, the attacker inherits that user's scope.

**Likelihood**: Low — depends on a compromise cleanrr has no visibility into (SIM swap, session hijack).

**Impact**: Bounded. Ownership checks (`identity.get_link` → Overseerr user resolution → per-request `owner_id` comparison) mean the attacker can only act on the *linked user's own* requests, not anyone else's, and cannot reach admin-only tools (`delete_torrent`) unless that specific Telegram ID is separately in `ADMIN_TELEGRAM_IDS`.

**Existing mitigation**: per-user ownership scoping is enforced at the tool layer, not just trusted from Telegram's identity claim.

**Residual risk**: accepted, inherent to any bot built on Telegram identity.

### 4. `/invite` code issued to the wrong recipient

**What**: `/invite <overseerr_username>` (admin-only) generates an 8-character link code, valid for `LINK_CODE_TTL_HOURS` (default 24h), redeemable by *whichever* Telegram account sends `/link <code>` first. There's no binding between "who the admin intended" and "who redeems it" until redemption happens.

**Likelihood**: Low — requires the code to leak or be guessed within the TTL window. The code is generated via `secrets.choice` over a 32-character alphabet at length 8 (`identity.py`'s `_CODE_ALPHABET`/`_CODE_LENGTH`) — not brute-forceable in a 24h window.

**Impact**: Medium if it happens — the wrong person gets bound to the intended Overseerr account's permissions (can cancel/re-search *that* account's requests).

**Existing mitigation**: code redemption is atomic and single-use (`UPDATE ... WHERE consumed_at IS NULL` in `identity.redeem_code` — a concurrent or repeat redemption attempt can't win a race against the first), and the default TTL is short. This is an operational/social-engineering risk (admin sends the code to the right person out-of-band), not a code-level flaw.

**Residual risk**: accepted — mitigate by treating link codes like any other one-time secret when sharing them out-of-band.

### 5. Denial of service via message/action flooding

**What**: Any Telegram user can message the bot; each message spawns/reuses a per-user Agent.

**Existing mitigation** (this is a place where the risk is already actively managed, not just noted): `TELEGRAM_MAX_MESSAGE_CHARS` rejects oversized messages before they reach Claude; `AgentPool` caps total concurrent per-user agents; `ConfirmationRegistry` caps both total pending confirmations (100) and per-user pending confirmations (3), specifically to stop "a single noisy client" from exhausting the global slots (see the docstring in `_registry.py`).

**Residual risk**: low. This is the one category where the codebase already treats DoS as a first-class concern with enforced numeric limits, not just documentation.

### 6. Supply chain (dependencies, CI/CD, container image)

Covered in depth by the CI/CD security posture rather than restated here: secret scanning (gitleaks), two independent SAST engines (semgrep, CodeQL), dependency vulnerability scanning (pip-audit, Dependabot with a 7-day cooldown), every GitHub Action pinned to a commit SHA, signed release images (Sigstore/cosign, keyless), and a container image stripped of build-only tooling (`pip`/`setuptools`/`wheel`) after install specifically because a real CVE was found in `pip`'s own vendored dependencies during this project's own hardening pass. See `.github/workflows/` and `CONTRIBUTING.md`'s "Dependency management" section.

## Explicitly out of scope

- **Host/OS-level security of the deployment machine** — cleanrr assumes a reasonably-secured Docker host; it can't defend against a compromised kernel, Docker daemon, or sibling container.
- **Telegram's own platform security** (Bot API compromise, Telegram-side account takeover mechanics) — outside cleanrr's control.
- **The \*arr stack's own security** (Overseerr/Sonarr/Radarr/qBittorrent vulnerabilities) — cleanrr is a client of these services, not their maintainer.
