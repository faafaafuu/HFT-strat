from __future__ import annotations

from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.config import Settings
from app.data.database import Database
from app.data.models import SignalModel
from app.logger import get_logger
from app.market.features import MarketFeatureStore
from app.telegram.formatters import format_paper_closed, format_paper_opened, format_signal_message
from app.telegram.keyboards import signal_alert_menu
from app.utils.time import utc_now


class TelegramService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        feature_store: MarketFeatureStore | None = None,
        config_path: str | Path = "config.yaml",
    ) -> None:
        self.settings = settings
        self.database = database
        self.feature_store = feature_store
        self.config_path = config_path
        self.log = get_logger("telegram")
        self.application: Application | None = None
        self.paused = False
        self.symbols: list[str] = []
        self.started_at = utc_now()
        self.last_heartbeat = self.started_at
        self.active_websocket_connections = 0
        self.last_signal_time = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram.enabled and self.settings.telegram.bot_token)

    @property
    def is_online(self) -> bool:
        return self.application is not None

    def is_authorized_update(self, update: Update) -> bool:
        allowed_users = self.settings.telegram.allowed_user_ids
        user = update.effective_user
        if allowed_users:
            return user is not None and user.id in allowed_users

        chat_id = self.settings.telegram.chat_id
        chat = update.effective_chat
        if chat_id and chat is not None:
            return str(chat.id) == str(chat_id)
        return False

    async def start(self) -> None:
        if not self.enabled:
            self.log.warning("Telegram disabled or token missing")
            return
        from app.telegram.commands import TelegramCommands

        self.application = Application.builder().token(self.settings.telegram.bot_token).build()
        self.application.add_error_handler(self._handle_error)
        commands = TelegramCommands(self)
        handlers = {
            "start": commands.start,
            "status": commands.status,
            "signals": commands.signals,
            "stats": commands.stats,
            "stats_today": commands.stats_today,
            "stats_week": commands.stats_week,
            "top_pairs": commands.top_pairs,
            "top_patterns": commands.top_patterns,
            "paper": commands.paper,
            "config": commands.config,
            "pause": commands.pause,
            "resume": commands.resume,
            "help": commands.help,
        }
        for command, callback in handlers.items():
            self.application.add_handler(CommandHandler(command, callback))
        self.application.add_handler(CallbackQueryHandler(commands.callback))
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is not None:
            await self.application.updater.start_polling()
        self.log.info("Telegram bot started")

    async def stop(self) -> None:
        if self.application is None:
            return
        if self.application.updater is not None:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def send_signal(
        self,
        signal: SignalModel,
        reasons: list[str],
        context: dict[str, object],
    ) -> None:
        if self.paused or not self.enabled or not self.settings.telegram.notifications_enabled:
            return
        chat_id = self.settings.telegram.chat_id
        if not chat_id:
            self.log.warning("Telegram chat id missing")
            return
        if self.application is not None:
            bot = self.application.bot
        else:
            from telegram import Bot

            bot = Bot(self.settings.telegram.bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=format_signal_message(signal, reasons, context, self.settings),
            reply_markup=signal_alert_menu(signal),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        self.last_signal_time = signal.timestamp

    async def send_paper_opened(self, trade, balance: float) -> None:
        if not self.enabled or not self.settings.telegram.notifications_enabled:
            return
        chat_id = self.settings.telegram.chat_id
        if not chat_id:
            return
        bot = self.application.bot if self.application is not None else None
        if bot is None:
            from telegram import Bot

            bot = Bot(self.settings.telegram.bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=format_paper_opened(trade, balance),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def send_paper_closed(self, trade, balance: float, winrate: float) -> None:
        if not self.enabled or not self.settings.telegram.notifications_enabled:
            return
        chat_id = self.settings.telegram.chat_id
        if not chat_id:
            return
        bot = self.application.bot if self.application is not None else None
        if bot is None:
            from telegram import Bot

            bot = Bot(self.settings.telegram.bot_token)
        await bot.send_message(
            chat_id=chat_id,
            text=format_paper_closed(trade, balance, winrate),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def _handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Exception):
            self.log.warning("Telegram update failed: %s", context.error)
        else:
            self.log.warning("Telegram update failed")
