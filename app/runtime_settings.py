"""Runtime overrides stored in the database, shared by the bot, worker and web processes.

config.yaml holds the defaults; anything toggled from the dashboard is persisted here so all
three processes converge on the same state without a restart.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.data.database import Database
from app.data.repositories import RuntimeSettingsRepository

REFRESH_INTERVAL_SECONDS = 15


async def apply_runtime_settings(database: Database, settings: Any) -> dict[str, Any]:
    async with database.session() as session:
        overrides = await RuntimeSettingsRepository(session).get_all()
    for key, value in overrides.items():
        set_nested(settings, key, value)
    return overrides


async def refresh_runtime_settings_loop(
    log: Any,
    database: Database,
    settings: Any,
    interval_seconds: int = REFRESH_INTERVAL_SECONDS,
) -> None:
    """Re-read overrides so dashboard toggles reach an already running process."""
    previous: dict[str, Any] = {}
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with database.session() as session:
                overrides = await RuntimeSettingsRepository(session).get_all()
        except Exception as exc:  # noqa: BLE001 - a transient DB error must not kill the loop.
            log.warning("runtime settings refresh failed: %s", exc)
            continue
        changed = {key: value for key, value in overrides.items() if previous.get(key) != value}
        if changed and previous:
            log.info("runtime settings changed keys=%s", ",".join(sorted(changed)))
        for key, value in changed.items():
            set_nested(settings, key, value)
        previous = overrides


def set_nested(settings: Any, key: str, value: Any) -> None:
    target = settings
    parts = key.split(".")
    for part in parts[:-1]:
        if isinstance(target, dict):
            target = target.get(part)
        else:
            target = getattr(target, part, None)
        if target is None:
            return
    if isinstance(target, dict):
        target[parts[-1]] = value
    elif hasattr(target, parts[-1]):
        setattr(target, parts[-1], value)
