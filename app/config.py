from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AppConfig(BaseModel):
    mode: Literal["signal_only", "paper_trading", "paper_signal"] = "signal_only"
    log_level: str = "INFO"


class TelegramConfig(BaseModel):
    enabled: bool = True
    notifications_enabled: bool = True
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    allowed_user_ids_env: str = "TELEGRAM_ALLOWED_USER_IDS"

    @property
    def bot_token(self) -> str | None:
        return os.getenv(self.bot_token_env)

    @property
    def chat_id(self) -> str | None:
        return os.getenv(self.chat_id_env)

    @property
    def allowed_user_ids(self) -> set[int]:
        raw = os.getenv(self.allowed_user_ids_env, "")
        result: set[int] = set()
        for item in raw.replace(";", ",").split(","):
            value = item.strip()
            if not value:
                continue
            try:
                result.add(int(value))
            except ValueError:
                continue
        return result


class BybitConfig(BaseModel):
    enabled: bool = True
    market_type: Literal["linear"] = "linear"
    testnet: bool = False
    ws_topics_per_connection: int = 20
    orderbook_depth_limit: int = 100
    orderbook_process_interval_ms: int = 250

    @field_validator("ws_topics_per_connection", "orderbook_depth_limit")
    @classmethod
    def bybit_positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("bybit integer settings must be positive")
        return value

    @field_validator("orderbook_process_interval_ms")
    @classmethod
    def bybit_non_negative_integer(cls, value: int) -> int:
        if value < 0:
            raise ValueError("bybit orderbook_process_interval_ms must be non-negative")
        return value


class HyperliquidConfig(BaseModel):
    enabled: bool = False
    testnet: bool = False


class ExchangesConfig(BaseModel):
    bybit: BybitConfig = Field(default_factory=BybitConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)


class SymbolsConfig(BaseModel):
    auto_select: bool = True
    max_symbols: int = 30
    min_24h_volume_usd: float = 20_000_000
    max_spread_pct: float = 0.05
    min_orderbook_depth_usd_1pct: float = 500_000
    exchanges: list[str] = Field(default_factory=lambda: ["bybit"])
    manual_list: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    @field_validator("max_symbols")
    @classmethod
    def max_symbols_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("symbols.max_symbols must be positive")
        return value


class SignalsConfig(BaseModel):
    min_score: int = 6
    strong_score: int = 8
    cooldown_minutes_per_symbol: int = 20


class ThresholdsConfig(BaseModel):
    price_change_5m_pct: float = 0.7
    oi_change_15m_pct: float = 2.0
    volume_spike_multiplier: float = 1.5
    sweep_lookback_minutes: int = 30
    sweep_return_minutes: int = 5
    density_min_usd: float = 500_000
    density_max_distance_pct: float = 0.3
    density_min_lifetime_sec: int = 10
    funding_extreme_pct: float = 0.03


class OutcomesConfig(BaseModel):
    horizons_minutes: list[int] = Field(default_factory=lambda: [5, 15, 30, 60, 180])
    tp_levels_pct: list[float] = Field(default_factory=lambda: [0.5, 1.0, 1.5])
    sl_levels_pct: list[float] = Field(default_factory=lambda: [0.3, 0.5, 0.7])


class StrategyProfileConfig(BaseModel):
    enabled: bool = True
    strategies: list[str] = Field(default_factory=list)
    min_score: int = 7
    symbols: str | list[str] = "auto"
    paper_profile: str = "aggressive"


class StrategyInstanceConfig(BaseModel):
    strategy_key: str
    enabled: bool = True
    min_score: int = 7
    paper_profile: str = "aggressive"
    symbols: str | list[str] = "auto"
    config: dict[str, object] = Field(default_factory=dict)


class StrategyProfilesConfig(BaseModel):
    profiles: dict[str, StrategyProfileConfig] = Field(
        default_factory=lambda: {
            "scalping_safe": StrategyProfileConfig(
                enabled=True,
                strategies=["stop_hunt_sweep", "micro_stop_hunt_reclaim", "failed_breakout_fade"],
                min_score=8,
                symbols="auto",
                paper_profile="conservative",
            ),
            "scalping_aggressive": StrategyProfileConfig(
                enabled=True,
                strategies=[
                    "oi_pump_price_move",
                    "micro_stop_hunt_reclaim",
                    "oi_momentum_scalper",
                    "trend_pullback_scalper",
                ],
                min_score=7,
                symbols="auto",
                paper_profile="aggressive",
            ),
        }
    )


class StrategyInstancesConfig(BaseModel):
    instances: dict[str, StrategyInstanceConfig] = Field(
        default_factory=lambda: {
            "density_conservative": StrategyInstanceConfig(
                strategy_key="density_strategy",
                enabled=True,
                min_score=8,
                paper_profile="conservative",
                config={
                    "min_density_usd": 1_000_000,
                    "max_distance_pct": 0.25,
                    "min_lifetime_sec": 20,
                    "require_absorption": True,
                    "require_trend_alignment": True,
                },
            ),
            "density_aggressive": StrategyInstanceConfig(
                strategy_key="density_strategy",
                enabled=True,
                min_score=7,
                paper_profile="aggressive",
                config={
                    "min_density_usd": 500_000,
                    "max_distance_pct": 0.4,
                    "min_lifetime_sec": 8,
                    "require_absorption": False,
                    "require_trend_alignment": False,
                },
            ),
        }
    )


class DensitySpoofDetectionConfig(BaseModel):
    enabled: bool = True
    pull_threshold_pct: float = 70
    pull_time_window_sec: int = 5


class DensityEatenDetectionConfig(BaseModel):
    enabled: bool = True
    eaten_threshold_pct: float = 70
    confirm_move_pct: float = 0.15
    confirm_window_sec: int = 20


class DensityRiskConfig(BaseModel):
    stop_behind_density_pct: float = 0.15
    take_profit_rr: float = 2.0
    max_holding_minutes: int = 60


class DensityStrategyConfig(BaseModel):
    enabled: bool = True
    min_density_usd: float = 500_000
    max_distance_pct: float = 0.35
    min_lifetime_sec: int = 10
    large_density_multiplier: float = 3.0
    relative_to_avg_depth: bool = True
    require_volume_spike: bool = True
    volume_spike_multiplier: float = 1.3
    require_absorption: bool = False
    absorption_min_trades_usd: float = 200_000
    absorption_price_move_max_pct: float = 0.08
    spoof_detection: DensitySpoofDetectionConfig = Field(
        default_factory=DensitySpoofDetectionConfig
    )
    eaten_detection: DensityEatenDetectionConfig = Field(
        default_factory=DensityEatenDetectionConfig
    )
    risk: DensityRiskConfig = Field(default_factory=DensityRiskConfig)


class ChannelStrategyConfig(BaseModel):
    enabled: bool = True
    # How many bars on each side a swing must dominate to count as a pivot.
    pivot_lookback: int = 3
    # Guards against drawing a channel across a few noisy bars.
    min_bars_between_points: int = 5
    # After point 3 the channel goes stale if the 4th touch never arrives.
    max_bars_wait_touch: int = 60
    # How close a wick must come to a boundary to count as a touch.
    touch_tolerance_pct: float = 0.10
    # How far a close must sit past a boundary before the channel is broken.
    breakout_buffer_pct: float = 0.10
    stop_pct: float = 1.0
    max_stop_pct: float = 1.5
    stop_buffer_pct: float = 0.05
    take_pct: float = 4.0
    min_rr: float = 2.0
    history_candles: int = 240
    # In candles, not minutes: a channel trade needs room to reach the far boundary.
    max_holding_candles: int = 48


class BacktestConfig(BaseModel):
    min_trades: int = 50
    default_days: int = 30
    taker_fee_pct: float = 0.055
    slippage_pct: float = 0.01
    backtest_mode: Literal[
        "candles_only", "candles_plus_density_events", "candles_plus_orderbook_snapshots"
    ] = "candles_only"


class WebConfig(BaseModel):
    session_secret_env: str = "WEB_SESSION_SECRET"
    session_cookie_name: str = "mh_session"
    session_cookie_secure: bool = False
    session_cookie_same_site: Literal["lax", "strict", "none"] = "lax"
    session_max_age_seconds: int = 86400


class StorageConfig(BaseModel):
    data_dir: str = "/app/data"
    logs_dir: str = "/app/logs"
    backups_dir: str = "/app/backups"
    persist_market_snapshots: bool = True
    market_snapshot_interval_sec: int = 60
    keep_raw_ticks_minutes: int = 30
    keep_orderbook_events_days: int = 30
    keep_market_snapshots_days: int = 90
    max_price_points_per_symbol: int = 3000
    max_trade_points_per_symbol: int = 3000
    max_oi_points_per_symbol: int = 1000
    backup_interval_hours: int = 24
    strategy_analysis_interval_hours: int = 24

    @field_validator(
        "market_snapshot_interval_sec",
        "keep_raw_ticks_minutes",
        "keep_orderbook_events_days",
        "keep_market_snapshots_days",
        "max_price_points_per_symbol",
        "max_trade_points_per_symbol",
        "max_oi_points_per_symbol",
        "backup_interval_hours",
        "strategy_analysis_interval_hours",
    )
    @classmethod
    def storage_positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("storage integer settings must be positive")
        return value


class PartialTakeProfitConfig(BaseModel):
    enabled: bool = True
    first_tp_pct: float = 50
    first_target_rr: float = 1


class TrailingConfig(BaseModel):
    enabled: bool = True
    activation_rr: float = 1
    distance_pct: float = 0.4


class PaperProfileConfig(BaseModel):
    name: str
    enabled: bool = True
    initial_balance: float = 2000
    min_score: int = 7
    risk_per_trade_pct: float = 0.5
    leverage: float = 5
    stop_loss_pct: float = 0.5
    take_profit_pct: float = 1.5
    max_open_positions: int = 3
    max_positions_per_symbol: int = 1
    max_daily_loss_pct: float = 3
    max_holding_minutes: int = 180
    breakeven_enabled: bool = True
    breakeven_activation_rr: float = 1.0
    trailing_enabled: bool = True
    trailing_activation_rr: float = 1.5
    trailing_distance_pct: float = 0.4
    allowed_patterns: list[str] = Field(default_factory=list)
    allowed_symbols: list[str] = Field(default_factory=list)
    blocked_symbols: list[str] = Field(default_factory=list)

    @field_validator(
        "initial_balance",
        "risk_per_trade_pct",
        "leverage",
        "stop_loss_pct",
        "take_profit_pct",
        "max_daily_loss_pct",
        "breakeven_activation_rr",
        "trailing_activation_rr",
        "trailing_distance_pct",
    )
    @classmethod
    def profile_non_negative_numbers(cls, value: float) -> float:
        if value < 0:
            raise ValueError("paper profile numeric settings must be non-negative")
        return value

    @field_validator(
        "min_score", "max_open_positions", "max_positions_per_symbol", "max_holding_minutes"
    )
    @classmethod
    def profile_positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("paper profile integer settings must be positive")
        return value


class PaperConfig(BaseModel):
    enabled: bool = True
    default_profile: str = "aggressive"
    profiles: dict[str, PaperProfileConfig] = Field(
        default_factory=lambda: {
            "conservative": PaperProfileConfig(
                name="Conservative",
                enabled=True,
                initial_balance=2000,
                min_score=8,
                risk_per_trade_pct=0.3,
                leverage=3,
                stop_loss_pct=0.4,
                take_profit_pct=1.2,
                max_open_positions=2,
                max_positions_per_symbol=1,
                max_daily_loss_pct=2,
                max_holding_minutes=180,
                breakeven_enabled=True,
                breakeven_activation_rr=1.0,
                trailing_enabled=True,
                trailing_activation_rr=1.5,
                trailing_distance_pct=0.3,
            ),
            "aggressive": PaperProfileConfig(
                name="Aggressive",
                enabled=True,
                initial_balance=2000,
                min_score=7,
                risk_per_trade_pct=0.7,
                leverage=7,
                stop_loss_pct=0.5,
                take_profit_pct=1.5,
                max_open_positions=3,
                max_positions_per_symbol=1,
                max_daily_loss_pct=3,
                max_holding_minutes=180,
                breakeven_enabled=True,
                breakeven_activation_rr=1.0,
                trailing_enabled=True,
                trailing_activation_rr=1.5,
                trailing_distance_pct=0.4,
            ),
            "experimental": PaperProfileConfig(
                name="Experimental",
                enabled=False,
                initial_balance=500,
                min_score=6,
                risk_per_trade_pct=1.0,
                leverage=10,
                stop_loss_pct=0.7,
                take_profit_pct=2.0,
                max_open_positions=5,
                max_positions_per_symbol=1,
                max_daily_loss_pct=5,
                max_holding_minutes=120,
                breakeven_enabled=False,
                trailing_enabled=True,
                trailing_activation_rr=1.3,
                trailing_distance_pct=0.5,
            ),
        }
    )
    initial_balance: float = 2000
    leverage: float = 5
    risk_per_trade_pct: float = 0.5
    max_open_positions: int = 3
    stop_pct: float = 0.5
    take_pct: float = 1.5
    auto_trade_min_score: int = 7
    taker_fee_pct: float = 0.055
    maker_fee_pct: float = 0.02
    slippage_pct: float = 0.01
    partial_tp: PartialTakeProfitConfig = Field(default_factory=PartialTakeProfitConfig)
    trailing: TrailingConfig = Field(default_factory=TrailingConfig)

    @field_validator(
        "initial_balance",
        "leverage",
        "risk_per_trade_pct",
        "stop_pct",
        "take_pct",
        "taker_fee_pct",
        "maker_fee_pct",
        "slippage_pct",
    )
    @classmethod
    def non_negative_numbers(cls, value: float) -> float:
        if value < 0:
            raise ValueError("paper numeric settings must be non-negative")
        return value

    @field_validator("max_open_positions", "auto_trade_min_score")
    @classmethod
    def positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("paper integer settings must be positive")
        return value


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:////app/data/bot.sqlite3"


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    exchanges: ExchangesConfig = Field(default_factory=ExchangesConfig)
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    outcomes: OutcomesConfig = Field(default_factory=OutcomesConfig)
    strategy_profiles: StrategyProfilesConfig = Field(default_factory=StrategyProfilesConfig)
    strategy_instances: StrategyInstancesConfig = Field(default_factory=StrategyInstancesConfig)
    density_strategy: DensityStrategyConfig = Field(default_factory=DensityStrategyConfig)
    channel_strategy: ChannelStrategyConfig = Field(default_factory=ChannelStrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(path: str | Path = "config.yaml") -> Settings:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text()) or {}
    return Settings.model_validate(raw)


def save_settings(settings: Settings, path: str | Path = "config.yaml") -> bool:
    config_path = Path(path)
    try:
        config_path.write_text(
            yaml.safe_dump(settings.model_dump(), sort_keys=False, allow_unicode=True),
        )
    except OSError:
        return False
    return True
