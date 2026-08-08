# Maintainers

cleanrr has a single maintainer.

| Name | GitHub | Role |
| --- | --- | --- |
| Rayan Alyasi | [@RayanAlyasi](https://github.com/RayanAlyasi) | Sole maintainer. Repository owner/admin, code review and merge authority, release management, security response. Holds the credentials for the deployed instance and the GHCR publishing target. |

## Sensitive access

The maintainer above is the only party with:

- Repository admin access (branch protection, secrets, settings).
- `RELEASE_PLEASE_TOKEN` and `CODECOV_TOKEN` repository secrets.
- Write access to the `ghcr.io/rayanalyasi/cleanrr` container registry namespace.
- Credentials for the live deployment (Telegram bot token, Anthropic auth, Overseerr/Sonarr/Radarr/qBittorrent API keys).

Release image signing uses Sigstore's keyless flow (GitHub OIDC) — there is no long-lived signing key to hold or rotate.

## Adding a maintainer

cleanrr currently has no process for this beyond the obvious, since there's only ever been one maintainer. Before anyone is granted write access or above (and specifically before anyone is granted repository admin or any of the sensitive access listed above), the existing maintainer(s) review the candidate's prior contributions and vouch for them directly — no anonymous or self-nominated escalation. This file is updated as part of that grant, not after the fact.
