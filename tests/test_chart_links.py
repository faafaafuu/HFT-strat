from app.telegram.charts import exchange_chart_url, tradingview_chart_url


def test_bybit_chart_links() -> None:
    assert exchange_chart_url("bybit", "BTCUSDT") == (
        "Bybit",
        "https://www.bybit.com/trade/usdt/BTCUSDT",
    )
    assert tradingview_chart_url("bybit", "BTCUSDT") == (
        "https://www.tradingview.com/chart/?symbol=BYBIT:BTCUSDT.P"
    )


def test_hyperliquid_chart_links() -> None:
    assert exchange_chart_url("hyperliquid", "BTCUSDT") == (
        "Hyperliquid",
        "https://app.hyperliquid.xyz/trade/BTC",
    )
    assert tradingview_chart_url("hyperliquid", "BTCUSDT") == (
        "https://www.tradingview.com/chart/?symbol=HYPERLIQUID:BTCUSD.P"
    )

