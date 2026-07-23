"""Сколько из убытка PUMPFUNUSDT 5m — издержки, и сравнение R/сделка с 1h мажорами."""
from app.research.data import load_series
from app.research.harness import CostModel, RiskModel, run
from app.research.stats import summarise
from app.research.strategies import build

pf = load_series("PUMPFUNUSDT", "5m", db_path="data/bot.sqlite3")
split = int(len(pf)*0.6)
risk = RiskModel(risk_pct=2.0, max_leverage=10.0, max_bars=480)

print("PUMPFUNUSDT 5m, Donchian rr=3, IS — вклад издержек:")
for label, costs in (("базовые 5m (0.055%+0.05%)", CostModel(0.055, 0.05)),
                     ("как на 1h (0.055%+0.02%)", CostModel(0.055, 0.02)),
                     ("нулевые издержки", CostModel(0.0, 0.0))):
    r = run(pf, build("donchian_breakout", rr=3), costs=costs, risk=risk, start=0, end=split)
    s = summarise(r)
    rpt = sum(t.r_multiple for t in r.trades)/len(r.trades)
    print(f"  {label:<28} PF {s['profit_factor']:.2f}  R/сделка {rpt:+.3f}  сделок {s['trades']}")

# сравнение R/сделка с 1h мажорами (базовые издержки каждого ТФ)
print("\nсравнение края (R/сделка), IS, базовые издержки своего ТФ:")
for sym, tf, db, slip in (("PUMPFUNUSDT","5m","data/bot.sqlite3",0.05),
                          ("BTCUSDT","1h","data/research.sqlite3",0.02),
                          ("ETHUSDT","1h","data/research.sqlite3",0.02),
                          ("SOLUSDT","1h","data/research.sqlite3",0.02)):
    s_ = load_series(sym, tf, db_path=db); sp = int(len(s_)*0.6)
    r = run(s_, build("donchian_breakout", rr=3), costs=CostModel(0.055, slip), risk=risk, start=0, end=sp)
    su = summarise(r)
    rpt = sum(t.r_multiple for t in r.trades)/len(r.trades) if r.trades else 0
    print(f"  {sym:<12} {tf}: R/сделка {rpt:+.3f}  PF {su.get('profit_factor',0):.2f}  сделок {su.get('trades',0)}")
