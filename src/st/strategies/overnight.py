"""隔夜腿：T 日收盤集合競價買進漲停股，T+1 開盤集合競價賣出。只用日 K。

游騰芳、何怡滿（2022）量的是 T+1 開盤 → 收盤（做空 A）；隔日沖賺的是 T 收盤 → T+1 開盤，
這一段論文沒有量。本模組把同一批事件拆成「隔夜」與「T+1 盤中」兩腿。

非當沖，證交稅 0.3%。買進以收盤價成交、不加滑價（限價單參加集合競價）；賣出加滑價但不低於 T+1 跌停價。

最大的問題是買不買得到：收盤鎖漲停時買單要排隊，日 K 看不出排不排得到，而且排得到的往往是
隔天開低的（逆選擇）。所以報表另外提供：
- 「收盤打開」組：盤中觸及漲停、收盤低於漲停價，收盤價一定買得到（但不是鎖漲停股，性質不同）
- fill_adjusted：T+1 開低／開平的一定成交、開高的只有 q 的機率成交，看 q 要多高才損益兩平

尚未排除處置股、注意股、全額交割股（名單資料還沒抓）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from st.core.costs import CostModel
from st.core.prices import shift_ticks
from st.strategies.short_a import Params as ShortAParams, _limit_price_eq, add_features


@dataclass(frozen=True)
class Params:
    markets: tuple[str, ...] = ("TWSE",)
    cost: CostModel = CostModel(discount=1.0, min_fee=20)


def build_trades(daily: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """T 日最高價觸及漲停的上市股，一列一個事件。close_at_limit 區分「收盤鎖住」與「收盤打開」。"""
    d = daily[daily["market"].isin(p.markets) & daily["kind"].eq("stock")]
    d = add_features(d.sort_values(["code", "date"]), ShortAParams(markets=p.markets))

    touched = d["limit_up"].notna() & (d["high"].round(2) >= d["limit_up"].round(2))
    tradable = (d["n_date"] == d["cal_next"]) & d["n_open"].notna() & d["n_close"].notna() & (d["n_open"] > 0)
    ev = d[touched & tradable & (d["close"] > 0)].copy()

    ev["close_at_limit"] = _limit_price_eq(ev["close"], ev["limit_up"])
    ev["locked_all_day"] = _limit_price_eq(ev["low"], ev["limit_up"])  # 一字漲停，幾乎買不到
    ev["gap_up"] = ev["n_open"] > ev["close"]
    # T+1 開盤即跌停：賣單可能排不到，實際虧損會比回測大。先標記，不剔除
    ev["open_at_limit_down"] = _limit_price_eq(ev["n_open"], ev["n_limit_down"])
    ev["open_at_limit_up"] = _limit_price_eq(ev["n_open"], ev["n_limit_up"])

    slip = p.cost.slippage_ticks
    ev["buy_px"] = ev["close"]
    ev["sell_px"] = [max(float(shift_ticks(Decimal(str(o)), -slip)), ld if ld == ld else 0.0)
                     for o, ld in zip(ev["n_open"], ev["n_limit_down"])]

    ev["gross_ret"] = ev["n_open"] / ev["close"] - 1
    ev["net_ret"] = ev["sell_px"] / ev["buy_px"] - 1 - p.cost.round_trip_pct(daytrade=False)
    ev["intraday_ret"] = ev["n_close"] / ev["n_open"] - 1  # T+1 開盤 → 收盤（多方視角，未扣成本）

    ev = ev.rename(columns={"date": "event_date", "n_date": "trade_date"})
    cols = ["event_date", "trade_date", "market", "code", "name", "close", "volume", "value",
            "n_open", "n_close", "buy_px", "sell_px", "gross_ret", "net_ret", "intraday_ret",
            "close_at_limit", "locked_all_day", "gap_up", "open_at_limit_down", "open_at_limit_up",
            "f_consolidation", "f_volume_high"]
    return ev[cols].sort_values(["trade_date", "code"]).reset_index(drop=True)


def fill_adjusted(trades: pd.DataFrame, q: float) -> dict:
    """逆選擇成交模型：T+1 沒開高的委託一定成交，開高的只有 q 的機率成交。

    回傳「每張委託期望淨報酬」（沒成交算 0）與「每筆成交平均淨報酬」。
    用到 T+1 開盤資訊，只能當敏感度分析，不能當濾網。
    """
    if trades.empty:
        return {"委託數": 0}
    w = trades["gap_up"].map({True: q, False: 1.0})
    filled = w.sum()
    pnl = (w * trades["net_ret"]).sum()
    return {"委託數": len(trades), "預期成交數": filled, "成交率": filled / len(trades),
            "每筆成交平均淨報酬": pnl / filled if filled else float("nan"),
            "每張委託期望淨報酬": pnl / len(trades)}


def breakeven_fill(trades: pd.DataFrame) -> float:
    """開高樣本至少要有多少比例買得到，整體才損益兩平。>1 表示全部買到也賠；0 表示怎樣都賺。"""
    up = trades.loc[trades["gap_up"], "net_ret"].sum()
    down = trades.loc[~trades["gap_up"], "net_ret"].sum()
    if down >= 0:
        return 0.0
    return float("inf") if up <= 0 else -down / up


FILTERS = {
    "收盤鎖漲停（全部）": lambda t: t[t["close_at_limit"]],
    "收盤鎖漲停，排除一字鎖": lambda t: t[t["close_at_limit"] & ~t["locked_all_day"]],
    "收盤鎖漲停＋盤整＋量創新高＋成交值>1億": lambda t: t[t["close_at_limit"] & t["f_consolidation"]
                                                & t["f_volume_high"] & (t["value"] > 1e8)],
    # 收盤價一定買得到，但這群是「漲停沒鎖住」，不是隔日沖鎖定的標的
    "收盤打開（觸及漲停、收盤未鎖）": lambda t: t[~t["close_at_limit"]],
}
