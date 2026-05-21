from contextvars import ContextVar

current_telegram_user_id: ContextVar[int] = ContextVar("current_telegram_user_id")
