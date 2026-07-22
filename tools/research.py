"""Research driver: run a hypothesis, split in/out of sample, report against the criteria.

Usage:
    .venv/bin/python -m tools.research run --strategy donchian_breakout --symbol BTCUSDT --tf 1h
    .venv/bin/python -m tools.research sweep --strategy donchian_breakout --symbol BTCUSDT --tf 1h
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

from app.research.data import Series, load_series
from app.research.harness import CostModel, RiskModel, RunResult, run
from app.research.stats import monthly_table, summarise, verdict
from app.research.strategies import build

RESULTS_DIR = Path("research/results")
IN_SAMPLE_FRACTION = 0.6


def split_index(series: Series, fraction: float = IN_SAMPLE_FRACTION) -> int:
    return int(len(series) * fraction)


def _costs(timeframe: str, multiplier: float = 1.0) -> CostModel:
    # Faster timeframes fill worse; the brief fixes the floor per timeframe.
    slippage = 0.05 if timeframe in {"1m", "5m", "15m"} else 0.02
    return CostModel(taker_fee_pct=0.055, slippage_pct=slippage * multiplier)


def _risk(args: argparse.Namespace) -> RiskModel:
    return RiskModel(
        risk_pct=args.risk,
        max_leverage=args.max_leverage,
        initial_equity=10_000.0,
        breakeven_at_r=args.breakeven,
        trail_from_r=args.trail_from,
        trail_distance_r=args.trail_distance,
        partial_at_r=args.partial_at,
        partial_fraction=args.partial_fraction,
        max_bars=args.max_bars,
    )


def _params(raw: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in raw:
        key, _, value = item.partition("=")
        params[key] = _coerce(value)
    return params


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def print_report(title: str, result: RunResult, *, show_months: bool = True) -> dict[str, Any]:
    summary = summarise(result)
    print(f"\n=== {title} ===")
    if not summary.get("trades"):
        print("  сделок нет")
        return summary
    print(
        f"  сделок {summary['trades']:>4}  winrate {summary['winrate_pct']:5.1f}%  "
        f"PF {summary['profit_factor']:5.2f}  доходность {summary['total_return_pct']:+9.1f}%  "
        f"DD {summary['max_drawdown_pct']:5.1f}%"
    )
    print(
        f"  месяц: медиана {summary['median_month_pct']:+6.1f}%  прибыльных "
        f"{summary['profitable_months_pct']:5.1f}%  худший {summary['worst_month_pct']:+6.1f}%  "
        f"сделок/мес {summary['trades_per_month']:4.1f}"
    )
    print(
        f"  ожидание {summary['expectancy_r']:+5.2f}R  плечо ср {summary['avg_leverage']:4.1f}x "
        f"макс {summary['max_leverage']:4.1f}x  комиссии {summary['fees_share_of_gross']:4.1f}% "
        f"от валовой прибыли"
    )
    if show_months:
        rows = monthly_table(result)
        print("  помесячно: " + "  ".join(f"{row.month} {row.return_pct:+.0f}%" for row in rows))
    return summary


def print_verdict(summary: dict[str, Any]) -> bool:
    outcome = verdict(summary)
    print("\n  Критерии приёмки (Этап 1):")
    for check in outcome["checks"]:
        mark = "✓" if check["passed"] else "✗"
        print(
            f"    {mark} {check['name']:<32} {check['value']:>9} "
            f"(нужно {check['direction']} {check['limit']}{check['unit']})"
        )
    print(f"  Итог: {'ПРОЙДЕНО' if outcome['passed'] else 'не пройдено'}")
    return bool(outcome["passed"])


def command_run(args: argparse.Namespace) -> None:
    series = load_series(args.symbol, args.tf, db_path=args.db)
    strategy = build(args.strategy, **_params(args.param))
    split = split_index(series)
    costs, risk = _costs(args.tf, args.cost_multiplier), _risk(args)
    print(
        f"{args.symbol} {args.tf}: {len(series)} свечей "
        f"{series.time[0]:%Y-%m-%d} .. {series.time[-1]:%Y-%m-%d}; "
        f"in-sample до {series.time[split]:%Y-%m-%d}"
    )
    scope = {
        "in-sample": (0, split),
        "out-of-sample": (split, len(series)),
        "весь период": (0, len(series)),
    }
    payload: dict[str, Any] = {
        "strategy": args.strategy,
        "params": _params(args.param),
        "symbol": args.symbol,
        "timeframe": args.tf,
        "risk": asdict(risk),
        "costs": asdict(costs),
        "sections": {},
    }
    for name, (start, end) in scope.items():
        if args.only and name != args.only:
            continue
        result = run(series, build(args.strategy, **_params(args.param)), costs=costs, risk=risk, start=start, end=end)
        summary = print_report(f"{args.symbol} {args.tf} — {name}", result, show_months=args.months)
        payload["sections"][name] = summary
        if name == "out-of-sample" and summary.get("trades"):
            print_verdict(summary)
    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = RESULTS_DIR / f"{args.strategy}-{args.symbol}-{args.tf}-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        print(f"\n  сохранено: {path}")
    del strategy


def command_sweep(args: argparse.Namespace) -> None:
    """Grid over in-sample only — out-of-sample must stay untouched during selection."""
    series = load_series(args.symbol, args.tf, db_path=args.db)
    split = split_index(series)
    costs, risk = _costs(args.tf, args.cost_multiplier), _risk(args)
    grid = {key: value for key, value in (item.split("=", 1) for item in args.grid)}
    keys = list(grid)
    options = [[_coerce(piece) for piece in grid[key].split(",")] for key in keys]
    rows = []
    for combination in product(*options):
        params = dict(zip(keys, combination, strict=True))
        result = run(series, build(args.strategy, **params), costs=costs, risk=risk, start=0, end=split)
        summary = summarise(result)
        if not summary.get("trades"):
            continue
        rows.append((summary, params))
    rows.sort(key=lambda item: item[0]["profit_factor"], reverse=True)
    print(f"\n{args.strategy} {args.symbol} {args.tf}: {len(rows)} комбинаций с сделками (in-sample)")
    print(f"{'PF':>6} {'сделок':>7} {'winrate':>8} {'медиана мес':>12} {'DD':>7}  параметры")
    for summary, params in rows[: args.top]:
        listing = " ".join(f"{key}={value}" for key, value in params.items())
        print(
            f"{summary['profit_factor']:6.2f} {summary['trades']:7d} "
            f"{summary['winrate_pct']:7.1f}% {summary['median_month_pct']:11.1f}% "
            f"{summary['max_drawdown_pct']:6.1f}%  {listing}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Исследование торговых стратегий")
    parser.add_argument("--db", default="data/bot.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("run", "sweep"):
        item = sub.add_parser(name)
        item.add_argument("--strategy", required=True)
        item.add_argument("--symbol", default="BTCUSDT")
        item.add_argument("--tf", default="1h")
        item.add_argument("--risk", type=float, default=2.0)
        item.add_argument("--max-leverage", type=float, default=10.0)
        item.add_argument("--breakeven", type=float, default=None)
        item.add_argument("--trail-from", type=float, default=None)
        item.add_argument("--trail-distance", type=float, default=1.0)
        item.add_argument("--partial-at", type=float, default=None)
        item.add_argument("--partial-fraction", type=float, default=0.5)
        item.add_argument("--max-bars", type=int, default=120)
        item.add_argument("--cost-multiplier", type=float, default=1.0)
        item.add_argument("--db", default="data/bot.sqlite3")
        if name == "run":
            item.add_argument("--param", action="append", default=[])
            item.add_argument("--months", action="store_true")
            item.add_argument("--save", action="store_true")
            item.add_argument("--only", default=None)
            item.set_defaults(func=command_run)
        else:
            item.add_argument("--grid", action="append", default=[])
            item.add_argument("--top", type=int, default=15)
            item.set_defaults(func=command_sweep)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
