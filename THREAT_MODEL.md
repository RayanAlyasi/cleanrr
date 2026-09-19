# Threat model

This is an internal security assessment, not an external audit or pentest. It's based on direct review of the current codebase (`cleanrr/permissions/`, `cleanrr/identity.py`, `cleanrr/link_migration.py`, `cleanrr/config.py`, `cleanrr/tools/*_write.py`, `cleanrr/handlers.py`) and the actual deployment shape (single Docker container on a homelab network, alongside Sonarr/Radarr/Overseerr/qBittorrent). It should be revisited whenever a new tool, a new external integration, or a change to the confirmation/identity model ships — not just on a calendar cadence.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the actor list and external interface table this assessment assumes.

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

**Existing mitigation**: the confirm/cancel gate plus the tool-layer's own ownership re-check (e.g. `remove_my_request` independently verifies `owner_id == caller_user_id` against Overseerr's own data, not just Claude's say-so — explicitly commented in code as defense-in-depth against "Overseerr's DELETE endpoint accepts any authenticated admin-API call"). Queue and tracker status text now reaches Claude through `cleanrr/tools/_untrusted.py`, which bounds the message count and length, replaces every non-printable character with a space, quotes the result, and labels it as data in the prompt. That label is defense in depth, not the control — it does not by itself prevent injection. The control is still the confirmation gate and the ownership re-check above. The CLI subprocess each `Agent` fronts is started with filesystem settings disabled and a pinned empty working directory (`cleanrr/agent.py`), so a `permissions.allow` entry in a user or project settings file cannot pre-approve a destructive tool and skip `can_use_tool`; enterprise managed settings are outside this control, and cleanrr's image ships no managed-settings file. `/reset` only ever resolves a pending confirmation as denied and can never approve one; a reset Agent's still-finishing turn is refused any destructive tool by `can_use_tool` before a prompt for it is even sent, so a reset can't be turned into an approval.

**Residual risk**: low, accepted. The human-in-the-loop confirmation is the actual control here, not output filtering on Claude's responses (which doesn't exist and isn't the right layer for this).

### 3. Telegram account takeover of a linked user

**What**: If a linked user's own Telegram account is compromised, the attacker inherits that user's scope.

**Likelihood**: Low — depends on a compromise cleanrr has no visibility into (SIM swap, session hijack).

**Impact**: Bounded. Ownership checks (`identity.get_linked_user` → `resolve_linked_user_id`, which trusts the id stored at link time and only falls back to Overseerr user resolution when there isn't one → per-request `owner_id` comparison) mean the attacker can only act on the *linked user's own* requests, not anyone else's, and cannot reach admin-only tools (`delete_torrent`) unless that specific Telegram ID is separately in `ADMIN_TELEGRAM_IDS`. Once a link's id is stored, a Plex or Jellyfin rename cannot re-point it. A link whose id is not stored yet still resolves by username on each call, and the startup backfill (`cleanrr/link_migration.py`) stores the id of the one account that matches the stored name exactly at that moment. A name that matches no account, or matches more than one of the accounts the search reaches, resolves to nothing, and nothing is stored. A stored id is trusted only while the `linked_at` and the username it was resolved for both still match the row, so a write by an image that predates those columns is read as having no stored id. A stored id that Overseerr no longer knows (a 404) is not re-resolved by username. Restoring or resetting Overseerr's own database can leave a stored id naming a different account, so re-issue link codes afterward.

**Existing mitigation**: per-user ownership scoping is enforced at the tool layer, not just trusted from Telegram's identity claim.

**Residual risk**: accepted, inherent to any bot built on Telegram identity.

### 4. `/invite` code issued to the wrong recipient

**What**: `/invite <overseerr_username>` (admin-only) generates an 8-character link code, valid for `LINK_CODE_TTL_HOURS` (default 24h), redeemable by *whichever* Telegram account sends `/link <code>` first. There's no binding between "who the admin intended" and "who redeems it" until redemption happens.

**Likelihood**: Low — requires the code to leak or be guessed within the TTL window. The code is generated via `secrets.choice` over a 31-character alphabet at length 8 (`identity.py`'s `_CODE_ALPHABET`/`_CODE_LENGTH`) — not brute-forceable in a 24h window.

**Impact**: Medium if it happens — the wrong person gets bound to the intended Overseerr account's permissions (can cancel/re-search *that* account's requests).

**Existing mitigation**: code redemption is atomic and single-use (`UPDATE ... WHERE consumed_at IS NULL` in `identity.redeem_code` — a concurrent or repeat redemption attempt can't win a race against the first), and the default TTL is short. The Overseerr user id is resolved and bound to the code at `/invite` time, so a redeemed code binds the account the admin named then, not whatever account holds that username by the time it's redeemed. This is an operational/social-engineering risk (admin sends the code to the right person out-of-band), not a code-level flaw.

**Residual risk**: accepted — mitigate by treating link codes like any other one-time secret when sharing them out-of-band.

### 5. Denial of service via message/action flooding

**What**: A linked user or admin can send messages as fast as Telegram allows, and each one is a Claude turn on that user's Agent. An unlinked stranger cannot reach Claude or the Agent pool, but can still make the bot send one refusal reply and write one log line per message.

**Existing mitigation** (this is a place where the risk is already actively managed, not just noted): the link gate itself (`identity.get_linked_user` / `settings.admin_telegram_ids`, checked in `on_message` via `_is_authorized`) keeps unauthorized senders out of the Agent pool entirely; `TELEGRAM_MAX_MESSAGE_CHARS` rejects oversized messages before they reach Claude; `AgentPool` caps concurrent per-user Agents at a fixed number, counting an Agent that is still stopping so a burst of `/reset`s can't push the real subprocess count above the cap; idle Agents are evicted after `AGENT_IDLE_TIMEOUT_MINUTES` of silence from that user, so someone who chats once does not hold a subprocess open until the bot restarts; `ConfirmationRegistry` caps both total pending confirmations (100) and per-user pending confirmations (3), specifically to stop "a single noisy client" from exhausting the global slots (see the docstring in `_registry.py`).

**Residual risk**: low. A linked user or admin can still flood the bot within those caps, and an unlinked stranger can still make the bot emit one refusal reply and one log line per message — Telegram's own flood limits are the only bound on that. A user cycling `/reset` and a message can hold pool slots that are still stopping, so the at-capacity refusal can briefly hit another user for the few seconds a stop takes to finish; the cap bounds how bad that gets and it clears itself once the stop completes. This is the one category where the codebase already treats DoS as a first-class concern with enforced numeric limits, not just documentation.

### 6. Supply chain (dependencies, CI/CD, container image)

Covered in depth by the CI/CD security posture rather than restated here: secret scanning (gitleaks), two independent SAST engines (semgrep, CodeQL), dependency vulnerability scanning (pip-audit, Dependabot with a 7-day cooldown), direct dependency versions pinned in `constraints.txt` so CI, the release image, and a contributor's venv install the same versions, every GitHub Action pinned to a commit SHA, signed release images (Sigstore/cosign, keyless), the image built and Trivy-scanned on every pull request rather than only at release — the gap that let a release build fail its scan unnoticed — and a container image stripped of build-only tooling (`pip`/`setuptools`/`wheel`) after install specifically because a real CVE was found in `pip`'s own vendored dependencies during this project's own hardening pass. See `.github/workflows/` and `CONTRIBUTING.md`'s "Dependency management" section.

## Explicitly out of scope

- **Host/OS-level security of the deployment machine** — cleanrr assumes a reasonably-secured Docker host; it can't defend against a compromised kernel, Docker daemon, or sibling container.
- **Telegram's own platform security** (Bot API compromise, Telegram-side account takeover mechanics) — outside cleanrr's control.
- **The \*arr stack's own security** (Overseerr/Sonarr/Radarr/qBittorrent vulnerabilities) — cleanrr is a client of these services, not their maintainer.
