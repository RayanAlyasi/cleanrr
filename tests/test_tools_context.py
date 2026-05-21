from __future__ import annotations

import pytest

from cleanrr.tools._context import current_telegram_user_id


@pytest.mark.asyncio
async def test_contextvar_reset_after_respond() -> None:
    """Verify ContextVar is reset after respond() returns."""
    token = current_telegram_user_id.set(42)
    current_telegram_user_id.reset(token)

    with pytest.raises(LookupError):
        current_telegram_user_id.get()
