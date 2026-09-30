"""做空 A 基準版：前日收盤漲停的上市股，T+1 開盤賣出（先賣後買）、收盤買回。

依據：游騰芳、何怡滿（2022）〈台灣股市漲停次日之當沖績效分析〉，
2017/4/28–2020/12/31，扣成本後勝率 52%、平均淨報酬 0.24%、盈虧比 1.06。

build_trades 只用日 K；停損與延後進場變體在 simulate_intraday（需 T+1 分 K）。
尚未排除處置股、變更交易、不能先賣後買的標的（名單資料還沒抓），結果會略為高估可交易性。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

import pandas as pd

from st.backtest.intraday import SHORT, Limits, Trade, slip, stop_fill, vwap
from st.core.calendar import next_day_map
from st.core.costs import CostModel
from st.core.prices import shift_ticks


@dataclass(frozen=True)
class Params:
    markets: tuple[str, ...] = ("TWSE",)  # 論文只用上市
    cost: CostModel = CostModel(discount=1.0, min_fee=20)  # 論文成本假設未知，預設用無折扣
    consolidation_days: int = 10  # 「漲停前盤整」的回看天數
    consolidation_range: float = 0.15  # 區間振幅上限
    volume_high_days: int = 5  # 「量創 N 日新高」


def _limit_price_eq(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.round(2) == b.round(2)


def add_features(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """在日 K 上加 T+1 欄位與濾網特徵。df 需依 (code, date) 排序。"""
    d = df.copy()
    g = d.groupby("code", sort=False)

    # 市場交易日曆：下一個交易日
    d["cal_next"] = d["date"].map(next_day_map(d["date"]))

    for col in ("date", "open", "close", "limit_up", "limit_down"):
        d[f"n_{col}"] = g[col].shift(-1)

    # 漲停前盤整：T-N..T-1 的（最高 / 最低 − 1）< 門檻
    n = p.consolidation_days
    prior_high = g["high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
    prior_low = g["low"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).min())
    d["f_consolidation"] = (prior_high / prior_low - 1) < p.consolidation_range

    # 量創 N 日新高：T 日成交量 >= 含 T 在內 N 日最大量
    m = p.volume_high_days
    vmax = g["volume"].transform(lambda s: s.rolling(m, min_periods=m).max())
    d["f_volume_high"] = d["volume"] >= vmax
    return d


def build_trades(daily: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    d = daily[daily["market"].isin(p.markets) & daily["kind"].eq("stock")]
    d = add_features(d.sort_values(["code", "date"]), p)

    limit_close = d["limit_up"].notna() & _limit_price_eq(d["close"], d["limit_up"])
    next_is_next_day = d["n_date"] == d["cal_next"]  # T+1 有交易（非暫停交易）
    tradable = d["n_open"].notna() & d["n_close"].notna() & (d["n_open"] > 0)
    ev = d[limit_close & next_is_next_day & tradable].copy()

    # 開盤跌停：賣單排隊不一定成交；收盤漲停：買回排隊不一定成交。先標記，不剔除
    ev["open_at_limit_down"] = _limit_price_eq(ev["n_open"], ev["n_limit_down"])
    ev["close_at_limit_up"] = _limit_price_eq(ev["n_close"], ev["n_limit_up"])

    slip = p.cost.slippage_ticks
    sell = [max(float(shift_ticks(Decimal(str(o)), -slip)), ld if ld == ld else 0.0)
            for o, ld in zip(ev["n_open"], ev["n_limit_down"])]
    buy = [min(float(shift_ticks(Decimal(str(c)), slip)), lu if lu == lu else float("inf"))
           for c, lu in zip(ev["n_close"], ev["n_limit_up"])]
    ev["sell_px"] = sell
    ev["buy_px"] = buy

    ev["gross_ret"] = (ev["n_open"] - ev["n_close"]) / ev["n_open"]
    ev["net_ret"] = (ev["sell_px"] - ev["buy_px"]) / ev["sell_px"] - p.cost.round_trip_pct(daytrade=True)

    ev = ev.rename(columns={"date": "event_date", "n_date": "trade_date"})
    cols = ["event_date", "trade_date", "market", "code", "name", "close", "volume", "value",
            "n_open", "n_close", "sell_px", "buy_px", "gross_ret", "net_ret",
            "f_consolidation", "f_volume_high", "open_at_limit_down", "close_at_limit_up"]
    return ev[cols].sort_values(["trade_date", "code"]).reset_index(drop=True)


@dataclass(frozen=True)
class IntradayParams:
    """做空 A 的分 K 版本。entry="open" 為論文基準；"delayed" 為 09:15 後跌破開盤價且在 VWAP 之下才空。"""
    entry: str = "open"
    delay_from: time = time(9, 15)
    entry_end: time = time(13, 0)
    use_stop: bool = True
    stop_pct: float = 0.03  # 開盤價 +3%
    exit_time: time | None = time(13, 20)  # None → 收盤集合競價回補（同論文）
    slippage_ticks: int = 1


def simulate_intraday(code: str, bars: pd.DataFrame, lim: Limits, prev_limit_price: float,
                      p: IntradayParams = IntradayParams()) -> Trade | None:
    """bars：T+1 的 1 分 K。prev_limit_price：T 日漲停價（= T 日收盤價）。"""
    rows = bars.reset_index(drop=True)
    times = rows["ts"].dt.time
    open_px = float(rows.at[0, "open"])

    if p.entry == "open":
        i = 0
    else:
        v = vwap(rows).to_numpy()
        i = next((k + 1 for k in range(len(rows) - 1)
                  if p.delay_from <= times[k] < p.entry_end
                  and rows.at[k, "close"] < open_px and rows.at[k, "close"] < v[k]), None)
        if i is None:
            return None
    if p.exit_time and times[i] >= p.exit_time:
        return None

    entry = slip(rows.at[i, "open"], SHORT, True, p.slippage_ticks, lim)
    stop = open_px * (1 + p.stop_pct)
    if open_px < prev_limit_price:  # 開在前日漲停價之下，「重新站回」前日漲停價也停損
        stop = min(stop, prev_limit_price)
    tr = Trade(code, SHORT, rows.at[i, "ts"], entry, info={"t1_open": open_px, "stop": stop})

    def cover(j: int, px: float, reason: str) -> None:
        tr.exit(rows.at[j, "ts"], slip(px, SHORT, False, p.slippage_ticks, lim), None, reason)

    for j in range(i, len(rows)):
        if p.exit_time and times[j] >= p.exit_time:
            cover(j, rows.at[j, "open"], "time")
            return tr
        if p.use_stop:
            px = stop_fill(rows.iloc[j], SHORT, stop)
            if px is not None:
                cover(j, px, "stop")
                return tr
    cover(len(rows) - 1, rows.at[len(rows) - 1, "close"], "close")
    return tr


FILTERS = {
    "基準（全部漲停股）": lambda t: t,
    "漲停前盤整": lambda t: t[t["f_consolidation"]],
    "量創 5 日新高": lambda t: t[t["f_volume_high"]],
    "盤整 且 量創新高": lambda t: t[t["f_consolidation"] & t["f_volume_high"]],
    # 診斷用：用到 T+1 收盤資訊，不能當實盤濾網，只用來看「買不回」的樣本拖累多少
    "[診斷] 排除 T+1 收盤漲停": lambda t: t[~t["close_at_limit_up"]],
}
