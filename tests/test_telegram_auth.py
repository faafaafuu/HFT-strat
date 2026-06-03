import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.data.database import Database
from app.telegram.bot import TelegramService
from app.telegram.commands import TelegramCommands


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


@pytest.mark.asyncio
async def test_callback_answers_before_background_processing(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    service = TelegramService(Settings(), Database("sqlite+aiosqlite:///:memory:"))
    commands = TelegramCommands(service)
    processed = asyncio.Event()

    async def slow_process(_, data: str) -> None:
        assert data == "dashboard"
        await asyncio.sleep(0.05)
        processed.set()

    commands._process_callback = slow_process  # type: ignore[method-assign]

    class Query:
        data = "dashboard"
        message = object()
        answered = False

        async def answer(self, text=None, show_alert=False):
            self.answered = True

    class Application:
        def create_task(self, coroutine, update=None, name=None):
            return asyncio.create_task(coroutine, name=name)

    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=333),
        effective_chat=SimpleNamespace(id=999),
        message=None,
    )
    context = SimpleNamespace(application=Application())

    await commands.callback(update, context)

    assert query.answered is True
    assert processed.is_set() is False
    await asyncio.wait_for(processed.wait(), timeout=1)
