"""用法：
  python -m st.backtest short_a [--discount 1.0] [--min-fee 20] [--slip 1] [--markets TWSE]

輸出：終端機報表，以及 data/results/short_a_trades.csv、short_a_report.txt
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from st.core.costs import CostModel
from st.data.build import load_daily
from st.data.fetch import DATA_DIR
from st.strategies import short_a

from .metrics import format_table, summarize
from .segments import split

RESULTS = DATA_DIR / "results"


def report_short_a(daily, p: short_a.Params) -> str:
    trades = short_a.build_trades(daily, p)
    RESULTS.mkdir(parents=True, exist_ok=True)
    trades.to_csv(RESULTS / "short_a_trades.csv", index=False, encoding="utf-8-sig")

    c = p.cost
    out = [
        "做空 A：前日收盤漲停 → T+1 開盤賣、收盤買",
        f"市場 {','.join(p.markets)}｜手續費折扣 {c.discount}｜滑價每邊 {c.slippage_ticks} 檔"
        f"｜來回成本 {c.round_trip_pct(daytrade=True):.3%}（不含滑價）",
        f"資料期間 {daily['date'].min().date()} ~ {daily['date'].max().date()}",
        "論文基準：勝率 52%、平均淨報酬 0.24%、盈虧比 1.06",
        "",
    ]

    segs = split(trades)
    out += ["== 基準版，各區段 ==", format_table({k: summarize(v) for k, v in segs.items() if len(v)}), ""]

    for seg in ("論文樣本 2017/4/28–2020/12/31", "樣本外 2021–"):
        t = segs[seg]
        out += [f"== 濾網比較：{seg} ==",
                format_table({k: summarize(f(t)) for k, f in short_a.FILTERS.items()}), ""]

    # 成本敏感度：同一批事件，只改成本假設
    rows = {}
    for disc, mf in ((1.0, 20), (0.2, 1)):
        for slip in (0, 1, 2):
            q = replace(p, cost=CostModel(discount=disc, min_fee=mf, slippage_ticks=slip))
            t = short_a.build_trades(daily, q)
            for seg in ("論文樣本 2017/4/28–2020/12/31", "樣本外 2021–"):
                s = split(t)[seg]
                rows[f"折扣{disc} 滑價{slip}檔｜{seg[:4]}"] = summarize(s)
    out += ["== 成本敏感度 ==", format_table(rows), ""]

    n = len(trades)
    if n:
        out.append(f"T+1 開盤即跌停（賣單可能排不到）：{trades['open_at_limit_down'].mean():.1%}；"
                   f"T+1 收盤漲停（可能買不回）：{trades['close_at_limit_up'].mean():.1%}")
    text = "\n".join(out)
    (RESULTS / "short_a_report.txt").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m st.backtest")
    ap.add_argument("strategy", choices=["short_a"])
    ap.add_argument("--discount", type=float, default=1.0)
    ap.add_argument("--min-fee", type=int, default=20)
    ap.add_argument("--slip", type=int, default=1)
    ap.add_argument("--markets", default="TWSE")
    a = ap.parse_args()

    daily = load_daily()
    p = short_a.Params(markets=tuple(a.markets.split(",")),
                       cost=CostModel(discount=a.discount, min_fee=a.min_fee, slippage_ticks=a.slip))
    print(report_short_a(daily, p))


if __name__ == "__main__":
    main()
