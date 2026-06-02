from types import SimpleNamespace

from app.config import Settings
from app.data.database import Database
from app.telegram.bot import TelegramService


def _update(user_id: int, chat_id: int):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
    )


def test_telegram_auth_uses_allowed_user_ids(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111,222")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    service = TelegramService(Settings(), Database("sqlite+aiosqlite:///:memory:"))

    assert service.is_authorized_update(_update(111, 999)) is True
    assert service.is_authorized_update(_update(333, 999)) is False


def test_telegram_auth_falls_back_to_chat_id(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    service = TelegramService(Settings(), Database("sqlite+aiosqlite:///:memory:"))

    assert service.is_authorized_update(_update(333, 999)) is True
    assert service.is_authorized_update(_update(333, 123)) is False
