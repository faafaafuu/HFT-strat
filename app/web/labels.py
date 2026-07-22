"""Human-readable names for the parameter keys shown in the dashboard.

The keys come from strategy configs and hyperopt rows, where short technical names are
right. In the UI they need to say what they actually control.
"""

from __future__ import annotations

from typing import Any

PARAMETER_LABELS: dict[str, tuple[str, str]] = {
    # key: (name, unit)
    "min_score": ("Минимальный скор", "из 10"),
    "stop_pct": ("Стоп-лосс", "%"),
    "max_stop_pct": ("Максимальный стоп", "%"),
    "stop_buffer_pct": ("Запас за уровнем стопа", "%"),
    "take_pct": ("Тейк-профит", "%"),
    "min_rr": ("Минимальное соотношение прибыль/риск", ""),
    "take_profit_rr": ("Тейк в единицах риска", "R"),
    "max_holding_minutes": ("Максимум в позиции", "мин"),
    "max_holding_candles": ("Максимум в позиции", "свечей"),
    # Channel strategy
    "pivot_lookback": ("Ширина окна для экстремума", "свечей"),
    "min_bars_between_points": ("Минимум свечей между точками канала", ""),
    "min_bars_before_touch": ("Пауза перед 4-м касанием", "свечей"),
    "max_bars_wait_touch": ("Ожидание 4-го касания", "свечей"),
    "touch_tolerance_pct": ("Допуск касания границы", "%"),
    "breakout_buffer_pct": ("Запас на пробой канала", "%"),
    "history_candles": ("Глубина истории для поиска канала", "свечей"),
    # Density strategy
    "min_density_usd": ("Минимальный объём плотности", "$"),
    "max_distance_pct": ("Максимальное расстояние до цены", "%"),
    "min_lifetime_sec": ("Минимальное время жизни заявки", "сек"),
    "require_absorption": ("Требовать поглощение", ""),
    "require_trend_alignment": ("Требовать совпадение с трендом", ""),
    "require_volume_spike": ("Требовать всплеск объёма", ""),
    "volume_spike_multiplier": ("Порог всплеска объёма", "×"),
    "large_density_multiplier": ("Во сколько раз плотность крупнее обычной", "×"),
    "relative_to_avg_depth": ("Сравнивать со средней глубиной стакана", ""),
    "absorption_min_trades_usd": ("Объём сделок для поглощения", "$"),
    "absorption_price_move_max_pct": ("Максимальный сдвиг цены при поглощении", "%"),
    "stop_behind_density_pct": ("Стоп за плотностью", "%"),
    "enabled": ("Включена", ""),
}

_BOOL_WORDS = {True: "да", False: "нет"}


def label_for(key: str) -> str:
    return PARAMETER_LABELS.get(key, (key.replace("_", " "), ""))[0]


def unit_for(key: str) -> str:
    return PARAMETER_LABELS.get(key, ("", ""))[1]


def format_value(key: str, value: Any) -> str:
    """Value plus its unit, with thousands separated and trailing zeros dropped."""
    if isinstance(value, bool):
        return _BOOL_WORDS[value]
    unit = unit_for(key)
    if isinstance(value, int | float):
        if unit == "$":
            return f"${value:,.0f}".replace(",", " ")
        text = f"{value:,.4f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
        text = text.replace(",", " ")
        return f"{text} {unit}".strip()
    return f"{value} {unit}".strip()


def describe(key: str, value: Any) -> str:
    return f"{label_for(key)}: {format_value(key, value)}"
