from __future__ import annotations

from urllib.parse import quote


def exchange_chart_url(exchange: str, symbol: str) -> tuple[str, str] | None:
    exchange_key = exchange.lower()
    if exchange_key == "bybit":
        return "Bybit", f"https://www.bybit.com/trade/usdt/{quote(symbol)}"
    if exchange_key == "hyperliquid":
        base = base_from_symbol(symbol)
        if not base:
            return None
        return "Hyperliquid", f"https://app.hyperliquid.xyz/trade/{quote(base)}"
    return None


def tradingview_chart_url(exchange: str, symbol: str) -> str | None:
    exchange_key = exchange.lower()
    if exchange_key == "bybit":
        return f"https://www.tradingview.com/chart/?symbol=BYBIT:{quote(symbol)}.P"
    if exchange_key == "hyperliquid":
        base = base_from_symbol(symbol)
        if not base:
            return None
        return f"https://www.tradingview.com/chart/?symbol=HYPERLIQUID:{quote(base)}USD.P"
    return None


def base_from_symbol(symbol: str) -> str:
    upper = symbol.upper()
    for suffix in ("USDT", "USD", "-PERP", "PERP"):
        if upper.endswith(suffix):
            return upper[: -len(suffix)]
    return upper
