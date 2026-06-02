from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.time import utc_now


class Base(DeclarativeBase):
    pass


class SymbolModel(Base):
    __tablename__ = "symbols"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_symbols_exchange_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    base: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    depth_1pct_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MarketSnapshotModel(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[float] = mapped_column(Float)
    volume_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_5m: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_5m: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_15m: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_depth_1pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_depth_1pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class OrderbookEventModel(Base):
    __tablename__ = "orderbook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifetime_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


class SignalModel(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "pattern", "timestamp", name="uq_signal_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    reasons_json: Mapped[str] = mapped_column(Text)
    market_context_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    manual_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    outcomes: Mapped[list["SignalOutcomeModel"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
    )


class SignalOutcomeModel(Base):
    __tablename__ = "signal_outcomes"
    __table_args__ = (UniqueConstraint("signal_id", "horizon_minutes", name="uq_signal_outcome_horizon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    horizon_minutes: Mapped[int] = mapped_column(Integer, index=True)
    price_after: Mapped[float] = mapped_column(Float)
    mfe_pct: Mapped[float] = mapped_column(Float)
    mae_pct: Mapped[float] = mapped_column(Float)
    hit_tp_0_5: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_tp_1_0: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_tp_1_5: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_sl_0_3: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_sl_0_5: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_sl_0_7: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    signal: Mapped[SignalModel] = relationship(back_populates="outcomes")


class PaperAccountModel(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, default="default")
    initial_balance: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PaperTradeModel(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    take_price: Mapped[float] = mapped_column(Float)
    leverage: Mapped[float] = mapped_column(Float)
    position_size_usd: Mapped[float] = mapped_column(Float)
    remaining_size_usd: Mapped[float] = mapped_column(Float)
    risk_usd: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fees_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    realized_rr: Mapped[float] = mapped_column(Float, default=0.0)
    partial_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    trailing_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    high_watermark: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_watermark: Mapped[float | None] = mapped_column(Float, nullable=True)


class PaperEquityCurveModel(Base):
    __tablename__ = "paper_equity_curve"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("paper_trades.id"), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    net_profit: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)


class PaperDailyStatsModel(Base):
    __tablename__ = "paper_daily_stats"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_paper_daily_stats_account_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    balance: Mapped[float] = mapped_column(Float)
    net_profit: Mapped[float] = mapped_column(Float)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    winrate_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
