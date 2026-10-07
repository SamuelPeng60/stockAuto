"""窒息量：原本有量、整理後量縮到極點、價格守住 → 在第一根長紅之前買進。只用日 K。

坊間說法是「窒息量之後容易噴出」，但多半是等長紅出現才回頭認定前一天是窒息量（事後定義）。
這裡只用 T 日收盤前已知的資訊定義訊號，T+1 開盤買進、持有 hold 個交易日後收盤賣出：

- 量縮到極點（f_dry）：T 日成交量 ≤ 近 lookback 日最大量 × dry_ratio
- 原本有量（f_liquid）：近 lookback 日平均成交值 ≥ min_value（排除本來就沒量的冷門股）
- 價格守住：收盤 ≥ ma_days 均線（f_above_ma）、距近 lookback 日最高價回檔 ≤ max_pullback（f_near_high）
- 整理（f_tight）：近 tight_days 日（最高 / 最低 − 1）≤ tight_range

同一檔連續多天符合時只取第一天（前 cooldown 個交易日內沒有訊號）。

報酬用「收盤 / 參考價」逐日連乘，除權息日不會被算成下跌。非當沖，證交稅 0.3%。
bench_ret 是同一進場日、同一流動性門檻下所有股票的平均報酬；excess_ret = 毛報酬 − bench_ret，
用來分辨賺的是訊號還是大盤。持有多日的樣本彼此重疊，t 值偏高。
尚未排除處置股、注意股、全額交割股（名單資料還沒抓）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from st.core.calendar import next_day_map
from st.core.costs import CostModel
from st.core.prices import shift_ticks


@dataclass(frozen=True)
class Params:
    markets: tuple[str, ...] = ("TWSE",)
    cost: CostModel = CostModel(discount=1.0, min_fee=20)
    lookback: int = 60  # 「近期」的回看天數（量的最大值、最高價、平均成交值）
    dry_ratio: float = 0.10  # 窒息量：當日量 ≤ 近期最大量的幾成
    min_value: float = 5e7  # 近期平均成交值下限（元）
    ma_days: int = 60
    max_pullback: float = 0.15  # 收盤距近期最高價的最大回檔
    tight_days: int = 5
    tight_range: float = 0.08
    cooldown: int = 5  # 前幾個交易日內有過訊號就不重複進場
    red_ret: float = 0.04  # 長紅：當日漲幅 ≥ 此值且收盤 > 開盤
    red_window: int = 10  # 訊號後幾個交易日內出現長紅算命中


def prepare(daily: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """加上訊號特徵與 T+1 欄位。特徵全部只用到 T 日（含）以前的資料。"""
    d = daily[daily["market"].isin(p.markets) & daily["kind"].eq("stock")]
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    g = d.groupby("code", sort=False)

    def roll(col: str, n: int, how: str) -> pd.Series:
        return getattr(g[col].rolling(n, min_periods=n), how)().reset_index(level=0, drop=True).sort_index()

    d["vol_max"] = roll("volume", p.lookback, "max")
    d["vol_ratio"] = d["volume"] / d["vol_max"]
    d["value_avg"] = roll("value", p.lookback, "mean")
    d["ma"] = roll("close", p.ma_days, "mean")
    d["pullback"] = 1 - d["close"] / roll("high", p.lookback, "max")
    d["range"] = roll("high", p.tight_days, "max") / roll("low", p.tight_days, "min") - 1

    # 逐日報酬用參考價算，除權息日的參考價已調整
    ok = (d["close"] > 0) & (d["ref"] > 0)
    d["day_ret"] = (d["close"] / d["ref"] - 1).where(ok)
    d["cum_log"] = np.log1p(d["day_ret"].fillna(0)).groupby(d["code"], sort=False).cumsum()
    d["is_red"] = (d["day_ret"] >= p.red_ret) & (d["close"] > d["open"])

    d["cal_next"] = d["date"].map(next_day_map(d["date"]))
    g = d.groupby("code", sort=False)  # 重新分組，納入上面新增的欄位
    for col in ("date", "open", "close", "limit_up", "cum_log"):
        d[f"n_{col}"] = g[col].shift(-1)

    # 訊號後第幾個交易日出現第一根長紅（red_window 內沒有就是 NaN）
    d["days_to_red"] = np.nan
    for k in range(p.red_window, 0, -1):
        d.loc[g["is_red"].shift(-k).eq(True), "days_to_red"] = k
    return d


def signal_flags(d: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    f = pd.DataFrame(index=d.index)
    f["f_dry"] = (d["volume"] > 0) & (d["vol_ratio"] <= p.dry_ratio)
    f["f_liquid"] = d["value_avg"] >= p.min_value
    f["f_above_ma"] = d["close"] >= d["ma"]
    f["f_near_high"] = d["pullback"] <= p.max_pullback
    f["f_tight"] = d["range"] <= p.tight_range
    return f


FLAGS = ("f_dry", "f_liquid", "f_above_ma", "f_near_high", "f_tight")


def first_of_cluster(d: pd.DataFrame, sig: pd.Series, cooldown: int) -> pd.Series:
    """同一檔前 cooldown 個交易日內沒有訊號的那幾天。"""
    if cooldown <= 0:
        return sig
    s = sig.astype(float)
    recent = s.groupby(d["code"], sort=False).transform(lambda x: x.shift(1).rolling(cooldown, min_periods=1).sum())
    return sig & ~(recent > 0)


def build_trades(d: pd.DataFrame, p: Params = Params(), hold: int = 5,
                 flags: tuple[str, ...] = FLAGS) -> pd.DataFrame:
    """d 是 prepare() 的結果。T 日訊號 → T+1 開盤買、T+hold 收盤賣；一列一筆。"""
    f = signal_flags(d, p)
    sig = first_of_cluster(d, f[list(flags)].all(axis=1), p.cooldown)

    g = d.groupby("code", sort=False)
    x_date, x_close, x_cum = g["date"].shift(-hold), g["close"].shift(-hold), g["cum_log"].shift(-hold)
    x_ld = g["limit_down"].shift(-hold)
    # T+1 有交易、出場日沒有跨資料缺口或長期停牌
    tradable = ((d["n_date"] == d["cal_next"]) & (d["n_open"] > 0) & (x_close > 0)
                & ((x_date - d["date"]).dt.days <= hold * 2 + 15))
    gross = d["n_close"] / d["n_open"] * np.exp(x_cum - d["n_cum_log"]) - 1

    # 對照組：同一進場日、過流動性門檻的全部股票
    uni = tradable & f["f_liquid"]
    bench = gross[uni].groupby(d.loc[uni, "date"]).mean()
    base_red = d.loc[uni, "days_to_red"].notna().mean()

    ev = d[sig & tradable].copy()
    ev["exit_date"], ev["x_close"] = x_date[ev.index], x_close[ev.index]
    ev["gross_ret"] = gross[ev.index]
    ev["bench_ret"] = ev["date"].map(bench)
    ev["excess_ret"] = ev["gross_ret"] - ev["bench_ret"]
    ev["red_hit"] = ev["days_to_red"].notna()
    ev["open_at_limit_up"] = ev["n_open"].round(2) == ev["n_limit_up"].round(2)  # 開盤即漲停，多半買不到

    slip = p.cost.slippage_ticks
    ev["buy_px"] = [min(float(shift_ticks(Decimal(str(o)), slip)), lu if lu == lu else float("inf"))
                    for o, lu in zip(ev["n_open"], ev["n_limit_up"])]
    ev["sell_px"] = [max(float(shift_ticks(Decimal(str(c)), -slip)), ld if ld == ld else 0.0)
                     for c, ld in zip(ev["x_close"], x_ld[ev.index])]
    ev["net_ret"] = ((1 + ev["gross_ret"]) * (ev["sell_px"] / ev["x_close"]) / (ev["buy_px"] / ev["n_open"])
                     - 1 - p.cost.round_trip_pct(daytrade=False))

    ev = ev.rename(columns={"date": "event_date", "n_date": "trade_date"})
    cols = ["event_date", "trade_date", "exit_date", "market", "code", "name", "close", "volume", "vol_ratio",
            "value_avg", "pullback", "range", "buy_px", "sell_px", "gross_ret", "bench_ret", "excess_ret",
            "net_ret", "red_hit", "days_to_red", "open_at_limit_up"]
    out = ev[cols].sort_values(["trade_date", "code"]).reset_index(drop=True)
    out.attrs["base_red_rate"] = float(base_red) if base_red == base_red else float("nan")
    return out


def scan(d: pd.DataFrame, p: Params = Params(), date=None) -> pd.DataFrame:
    """某一天（預設資料最後一天）符合全部條件的股票，量縮最嚴重的排前面。不套 cooldown。"""
    date = d["date"].max() if date is None else pd.Timestamp(date)
    f = signal_flags(d, p)
    hit = d[f[list(FLAGS)].all(axis=1) & (d["date"] == date)]
    cols = ["date", "market", "code", "name", "close", "volume", "vol_ratio", "value_avg", "pullback", "range"]
    return hit[cols].sort_values("vol_ratio").reset_index(drop=True)
