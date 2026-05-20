---
paths:
  - "cleanrr/config.py"
  - "cleanrr/identity.py"
  - "cleanrr/bot.py"
---

# Secrets and auth — cleanrr

- Every token-shaped setting is `pydantic.SecretStr`. Plain `str` for tokens is a bug.
- To use a secret value, call `.get_secret_value()`. Never put it in an f-string that might end up in logs or a Telegram reply.
- Never log secrets, even at DEBUG. Log presence/absence (`bool(token)`), not the value.
- Telegram input is untrusted. Don't pass `update.message.text` or `context.args` straight into a shell command, file path, SQL query, or another HTTP request body.
- Every command that mutates state (issuing codes, deleting, force-actions in later phases) must gate on `update.effective_user.id in settings.admin_telegram_ids`. The gate is the first thing the handler does after the None-checks.
- HTTP responses from the *arr stack are untrusted. Validate the shape before consuming. Default-deny on unexpected fields.
- `httpx` clients always set an explicit `timeout`. The default is unbounded — that's a DoS vector.
- Errors from external services must be caught and turned into a graceful Telegram reply. Never let an exception bubble into the user's chat as a traceback.
