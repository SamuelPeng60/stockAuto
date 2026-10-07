"""整理區間突破：不預測哪一天噴，等當天盤中帶量突破整理區間上緣才進場。

窒息量（dryup）的日 K 回測顯示「量縮後提前買」沒有優勢，所以改成等突破發生。
select_candidates 只用日 K：T−1 收盤後就能列出「在整理的股票」與突破價（box_high），
T 日最高價 > box_high 才算觸發，也才需要 T 日的分 K。盤中進出場的模擬要等分 K 資料到位再寫。

整理的定義（全部用 T−1 以前的資料）：
- 近 lookback 日平均成交值 ≥ min_value
- T−1 收盤 ≥ ma_days 均線，且距近 lookback 日最高價回檔 ≤ max_pullback
- 近 box_days 日（最高 / 最低 − 1）≤ box_range；box_high 就是這段的最高價

尚未排除處置股、注意股、全額交割股（名單資料還沒抓）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from st.core.calendar import prev_day_map


@dataclass(frozen=True)
class Params:
    markets: tuple[str, ...] = ("TWSE",)
    lookback: int = 60
    min_value: float = 5e7
    ma_days: int = 60
    max_pullback: float = 0.15
    box_days: int = 10
    box_range: float = 0.10


def select_candidates(daily: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """T 日最高價突破整理區間上緣的 (code, trade_date)。gap_open = 開盤就已經在 box_high 之上。"""
    d = daily[daily["market"].isin(p.markets) & daily["kind"].eq("stock")]
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    g = d.groupby("code", sort=False)

    def prior(col: str, n: int, how: str) -> pd.Series:
        """T−n..T−1 的滾動統計（不含 T 日）。"""
        r = getattr(g[col].rolling(n, min_periods=n), how)().reset_index(level=0, drop=True).sort_index()
        return r.groupby(d["code"], sort=False).shift(1)

    d["box_high"] = prior("high", p.box_days, "max")
    d["box_range"] = d["box_high"] / prior("low", p.box_days, "min") - 1
    d["value_avg"] = prior("value", p.lookback, "mean")
    d["prev_close"] = g["close"].shift(1)
    d["pullback"] = 1 - d["prev_close"] / prior("high", p.lookback, "max")
    d["vol_avg"] = prior("volume", 20, "mean")  # 盤中判斷「帶量」的基準

    ok = ((g["date"].shift(1) == d["date"].map(prev_day_map(d["date"])))  # 前一列就是前一個交易日
          & (d["value_avg"] >= p.min_value)
          & (d["prev_close"] >= prior("close", p.ma_days, "mean"))
          & (d["pullback"] <= p.max_pullback)
          & (d["box_range"] <= p.box_range)
          & (d["open"] > 0) & (d["high"] > d["box_high"]))
    c = d[ok].copy()
    c["gap_open"] = c["open"] > c["box_high"]
    c = c.rename(columns={"date": "trade_date"})
    cols = ["trade_date", "market", "code", "name", "box_high", "box_range", "pullback", "value_avg", "vol_avg",
            "prev_close", "open", "high", "close", "volume", "limit_up", "gap_open"]
    return c[cols].sort_values(["trade_date", "code"]).reset_index(drop=True)
