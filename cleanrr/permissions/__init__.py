"""Destructive-action confirmation flow.

When Claude calls a tool listed in ``WRITE_TOOLS``, the SDK's ``can_use_tool``
callback intercepts the call, posts a Telegram message with Confirm/Cancel
buttons, suspends until the user clicks (or the TTL elapses), and returns
``PermissionResultAllow`` / ``PermissionResultDeny`` accordingly. Read-only
tools allow immediately without sending a message.
"""

from __future__ import annotations

from cleanrr.permissions._callback import (
    CALLBACK_PREFIX,
    WRITE_TOOLS,
    CanUseTool,
    make_can_use_tool,
)
from cleanrr.permissions._formatters import ConfirmationFormatter, build_confirmation_formatters
from cleanrr.permissions._registry import ConfirmationRegistry, Outcome, PendingConfirmation

__all__ = [
    "CALLBACK_PREFIX",
    "WRITE_TOOLS",
    "CanUseTool",
    "ConfirmationFormatter",
    "ConfirmationRegistry",
    "Outcome",
    "PendingConfirmation",
    "build_confirmation_formatters",
    "make_can_use_tool",
]
