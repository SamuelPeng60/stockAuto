"""用法：
  python -m st.backtest short_a [--discount 1.0] [--min-fee 20] [--slip 1] [--markets TWSE]
  python -m st.backtest overnight [同上]

輸出：終端機報表，以及 data/results/<策略>_trades.csv、<策略>_report.txt
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from st.core.costs import CostModel
from st.data.build import load_daily
from st.data.fetch import DATA_DIR
from st.strategies import orb, overnight, short_a

from .metrics import format_table, summarize
from .segments import split

RESULTS = DATA_DIR / "results"


def _tag(markets) -> str:
    """輸出檔名的市場後綴；預設的上市不加，避免不同市場的結果互相覆蓋。"""
    return "" if tuple(markets) == ("TWSE",) else "_" + "_".join(markets).lower()


def report_short_a(daily, p: short_a.Params) -> str:
    trades = short_a.build_trades(daily, p)
    RESULTS.mkdir(parents=True, exist_ok=True)
    trades.to_csv(RESULTS / f"short_a{_tag(p.markets)}_trades.csv", index=False, encoding="utf-8-sig")

    c = p.cost
    out = [
        "做空 A：前日收盤漲停 → T+1 開盤賣、收盤買",
        f"市場 {','.join(p.markets)}｜手續費折扣 {c.discount}｜滑價每邊 {c.slippage_ticks} 檔"
        f"｜來回成本 {c.round_trip_pct(daytrade=True):.3%}（不含滑價）",
        f"開盤門檻 {'無' if p.min_gap is None else f'開盤漲幅 > {p.min_gap:.1%}'}"
        f"｜停損 {'無（抱到收盤）' if p.stop_pct is None else f'開盤價 +{p.stop_pct:.1%}'}",
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

    # 只空開高 × 日 K 停損：同一成本假設下，比較開盤位置門檻與停損幅度
    rows = {}
    for gap in (None, 0.0, 0.02):
        for stop in (None, 0.01, 0.02, 0.03, 0.05):
            t = short_a.build_trades(daily, replace(p, min_gap=gap, stop_pct=stop))
            t = t[~t["open_at_limit_up"]]  # 開盤即漲停：停損價高於漲停價、整天鎖住就買不回，不列入
            g = "全部" if gap is None else f"開高>{gap:.0%}"
            s = "抱到收盤" if stop is None else f"停損{stop:.0%}"
            for seg in ("論文樣本 2017/4/28–2020/12/31", "樣本外 2021–"):
                x = split(t)[seg]
                if len(x):
                    rows[f"{g}｜{s}｜{seg[:4]}"] = summarize(x) | {
                        "停損率": x["stopped"].mean(), "收盤漲停": x["close_at_limit_up"].mean()}
    out += ["== 只空開高 × 停損（排除開盤即漲停；停損假設一定在停損價成交，偏樂觀）==", format_table(rows), ""]

    n = len(trades)
    if n:
        out.append(f"T+1 開盤即跌停（賣單可能排不到）：{trades['open_at_limit_down'].mean():.1%}；"
                   f"T+1 收盤漲停（可能買不回）：{trades['close_at_limit_up'].mean():.1%}")
    text = "\n".join(out)
    (RESULTS / f"short_a{_tag(p.markets)}_report.txt").write_text(text, encoding="utf-8")
    return text


def _two_periods(t) -> dict:
    """目前日 K 只有這兩段（2020–2022 尚未下載）。"""
    segs = {"2017/4–2019": t[t["trade_date"] < "2020-01-01"], "2023–": t[t["trade_date"] >= "2023-01-01"]}
    return {k: v for k, v in segs.items() if len(v)}


def _gross_table(groups: dict, cols: dict[str, str]) -> str:
    """各組的毛報酬（未扣成本、無滑價）：筆數、平均、勝率、t 值。cols = {顯示名稱: 欄位}。"""
    rows = {}
    for name, t in groups.items():
        if not len(t):
            continue
        r = {"筆數": f"{len(t):,}"}
        for label, col in cols.items():
            s = summarize(t, ret_col=col)
            r[f"{label}平均"] = f"{s['平均淨報酬']:+.3%}"
            r[f"{label}勝率"] = f"{s['勝率']:.1%}"
            r[f"{label}t值"] = f"{s['t值']:.1f}"
        rows[name] = r
    return pd.DataFrame(rows).T.to_string() if rows else "(無資料)"


def report_overnight(daily, p: overnight.Params) -> str:
    trades = overnight.build_trades(daily, p)
    RESULTS.mkdir(parents=True, exist_ok=True)
    trades.to_csv(RESULTS / f"overnight{_tag(p.markets)}_trades.csv", index=False, encoding="utf-8-sig")

    c = p.cost
    locked = trades[trades["close_at_limit"]]
    periods = {k: v for k, v in split(locked).items() if len(v)}
    years = "、".join(str(y) for y in sorted(trades["trade_date"].dt.year.unique()))
    out = [
        "隔夜腿：T 日收盤漲停買進 → T+1 開盤賣出（非當沖，證交稅 0.3%）",
        f"市場 {','.join(p.markets)}｜手續費折扣 {c.discount}｜賣出滑價 {c.slippage_ticks} 檔（買進以收盤價、無滑價）"
        f"｜來回成本 {c.round_trip_pct(daytrade=False):.3%}（不含滑價）",
        f"資料期間 {daily['date'].min().date()} ~ {daily['date'].max().date()}；有資料的年度：{years}",
        "※ 以下「收盤鎖漲停」各表一律假設收盤排隊 100% 買得到，是樂觀上限；買不到的影響見最後的成交機率表。",
        "",
        "== 收盤鎖漲停，各區段（淨報酬）==", format_table({k: summarize(v) for k, v in periods.items()}), "",
        "== 同一批事件拆兩腿（毛報酬，未扣成本）：隔夜 = T 收→T+1 開；盤中 = T+1 開→收（做空 A 賺的是它的負值）==",
        _gross_table(periods, {"隔夜": "gross_ret", "盤中": "intraday_ret"}), "",
    ]

    main_segs = _two_periods(locked)
    for seg, t in _two_periods(trades).items():
        out += [f"== 分組比較：{seg}（淨報酬）==",
                format_table({k: summarize(f(t)) for k, f in overnight.FILTERS.items()}), ""]

    rows = {}
    for disc, mf in ((1.0, 20), (0.2, 1)):
        for slip in (0, 1, 2):
            q = replace(p, cost=CostModel(discount=disc, min_fee=mf, slippage_ticks=slip))
            t = overnight.build_trades(daily, q)
            for seg, s in _two_periods(t[t["close_at_limit"]]).items():
                rows[f"折扣{disc} 滑價{slip}檔｜{seg}"] = summarize(s)
    out += ["== 成本敏感度（收盤鎖漲停）==", format_table(rows), ""]

    out.append("== 成交機率敏感度（收盤鎖漲停）：T+1 沒開高的一定成交，開高的只有 q 的機率買得到 ==")
    for seg, t in main_segs.items():
        fa = pd.DataFrame({f"q={q:.0%}": overnight.fill_adjusted(t, q) for q in (1, 0.5, 0.3, 0.2, 0.1, 0)}).T
        for col in ("成交率", "每筆成交平均淨報酬", "每張委託期望淨報酬"):
            fa[col] = fa[col].map("{:+.3%}".format if "報酬" in col else "{:.1%}".format)
        for col in ("委託數", "預期成交數"):
            fa[col] = fa[col].map("{:,.0f}".format)
        out += [f"-- {seg}｜T+1 開高比例 {t['gap_up'].mean():.1%}｜損益兩平所需 q = {overnight.breakeven_fill(t):.1%} --",
                fa.to_string(), ""]

    # 議題 2 的證據：做多 A「開高就續抱一半」等於在賭開高之後盤中還會漲
    cond = {}
    for seg, t in main_segs.items():
        cond[f"{seg}｜T+1 開高"] = t[t["gap_up"]]
        cond[f"{seg}｜T+1 開平或開低"] = t[~t["gap_up"]]
    out += ["== T+1 開盤 → 收盤（毛報酬，多方視角），依開盤位置分組 ==",
            "（日 K 只能量到收盤；做多 A 實際最晚抱到 09:30，需分 K 才能精確驗證）",
            _gross_table(cond, {"盤中": "intraday_ret"}), ""]

    # 議題 3 的證據：做多 B 候選股中，前日收漲停與否的 T 日開盤 → 收盤
    cands = orb.select_candidates(daily[daily["market"].isin(p.markets)], replace(orb.LONG_B, exclude_prev_limit_up=False))
    px = daily[["date", "code", "open", "close"]].rename(columns={"date": "trade_date"})
    cands = cands.merge(px, on=["trade_date", "code"]).query("open > 0")
    cands["intraday_ret"] = cands["close"] / cands["open"] - 1
    grp = {}
    for seg, s in _two_periods(cands).items():
        grp[f"{seg}｜前日收漲停"] = s[s["prev_close_limit_up"]]
        grp[f"{seg}｜其餘候選"] = s[~s["prev_close_limit_up"]]
    out += ["== 做多 B 候選股的當日開盤 → 收盤（毛報酬，多方視角）==",
            "（只是選股池的方向性檢查，不是 ORB 進出場的績效；ORB 本身需分 K）",
            _gross_table(grp, {"盤中": "intraday_ret"}), ""]

    n = len(locked)
    if n:
        out.append(f"收盤鎖漲停 {n:,} 筆中：一字鎖（整天買不到）{locked['locked_all_day'].mean():.1%}；"
                   f"T+1 開盤即漲停 {locked['open_at_limit_up'].mean():.1%}；"
                   f"T+1 開盤即跌停（賣單可能排不到）{locked['open_at_limit_down'].mean():.1%}")
    text = "\n".join(out)
    (RESULTS / f"overnight{_tag(p.markets)}_report.txt").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m st.backtest")
    ap.add_argument("strategy", choices=["short_a", "overnight"])
    ap.add_argument("--discount", type=float, default=1.0)
    ap.add_argument("--min-fee", type=int, default=20)
    ap.add_argument("--slip", type=int, default=1)
    ap.add_argument("--markets", default="TWSE")
    ap.add_argument("--min-gap", type=float, default=None, help="做空 A：只空 T+1 開盤漲幅 > 此值（0 = 只空開高）")
    ap.add_argument("--stop", type=float, default=None, help="做空 A：漲到開盤價 ×(1+stop) 就回補，例如 0.03")
    ap.add_argument("--start", default=None, help="只用這一天以後的日 K，例如 2024-01-01")
    a = ap.parse_args()

    daily = load_daily()
    if a.start:
        daily = daily[daily["date"] >= a.start]
    if a.strategy == "overnight":
        print(report_overnight(daily, overnight.Params(
            markets=tuple(a.markets.split(",")),
            cost=CostModel(discount=a.discount, min_fee=a.min_fee, slippage_ticks=a.slip))))
        return
    p = short_a.Params(markets=tuple(a.markets.split(",")), min_gap=a.min_gap, stop_pct=a.stop,
                       cost=CostModel(discount=a.discount, min_fee=a.min_fee, slippage_ticks=a.slip))
    print(report_short_a(daily, p))


if __name__ == "__main__":
    main()
