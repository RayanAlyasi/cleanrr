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
