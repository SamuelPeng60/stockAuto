"""做多 A：尾盤鎖漲停隔日沖。報告證據評級 C，參數皆未驗證。

T 日：13:00 前已觸及漲停，13:25 時仍在漲停價 → 以漲停價掛 ROD 買單參加收盤集合競價，沒成交就放棄。
      濾網：漲停前 10 日盤整（振幅 <15%）、量創 5 日新高、當日成交值 >1 億。
T+1：開盤集合競價全部出清（open_sell_frac=1.0，預設）。
     做空 A 的證據是漲停股次日開盤後偏弱，續抱等於和做空 A 在同一時段對賭；日 K 回測（st.backtest overnight）
     也顯示開高後「開盤 → 收盤」平均為負。
     舊版（open_sell_frac=0.5）保留作對照：開盤為平盤以下全部出清；否則開盤賣 1/2，其餘 09:00 起 1 分 K
     收盤跌破開盤價 → 下一根開盤出場，最晚 exit_by 全部出場。有分 K 後再驗證 09:00–09:30 這段。
非當沖，證交稅 0.3%。

收盤排隊能否買到無法從 K 棒得知，提供三種成交假設（fill_mode）：
- "all"：一律以收盤價成交（樂觀上限）
- "opened"：只有收盤價低於漲停價（漲停被打開、排隊一定輪得到）才成交——逆選擇最嚴重的情境
- "worst"：只有 T+1 開盤價低於 T 日收盤價的樣本才算成交（報告建議的最差情境）

尚未排除處置股、注意股、全額交割股（名單資料還沒抓）；也無法檢查「下單張數 ≤ 漲停委買量 5%」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

from st.backtest.intraday import LONG, Limits, Trade, at, slip
from st.strategies.short_a import Params as ShortAParams, add_features


@dataclass(frozen=True)
class Params:
    touch_before: time = time(13, 0)
    check_at: time = time(13, 25)  # 用這個時間以前最後一根 K 棒收盤判斷是否仍在漲停
    min_value: float = 1e8
    use_consolidation: bool = True
    use_volume_high: bool = True
    fill_mode: str = "all"
    open_sell_frac: float = 1.0  # 開盤集合競價賣出比例；<1 時剩餘部位續抱到跌破開盤價或 exit_by
    exit_by: time = time(9, 30)
    slippage_ticks: int = 1


def select_candidates(daily: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """T 日盤中曾觸及漲停的股票（盤中條件交給分 K 判斷），附 T+1 日資料。"""
    d = daily[daily["kind"].eq("stock")].sort_values(["code", "date"])
    d = add_features(d, ShortAParams())
    touched = d["limit_up"].notna() & (d["high"].round(2) >= d["limit_up"].round(2))
    ok = touched & (d["value"] > p.min_value) & (d["n_date"] == d["cal_next"]) & d["n_open"].notna()
    if p.use_consolidation:
        ok &= d["f_consolidation"]
    if p.use_volume_high:
        ok &= d["f_volume_high"]
    c = d[ok]
    return c.rename(columns={"date": "event_date", "n_date": "next_date"})[
        ["event_date", "next_date", "market", "code", "name", "close", "limit_up", "limit_down",
         "n_limit_up", "n_limit_down", "value"]].reset_index(drop=True)


def simulate(code: str, bars_t: pd.DataFrame, bars_t1: pd.DataFrame, lim_t: Limits, lim_t1: Limits,
             p: Params = Params()) -> Trade | None:
    # ---- T 日：是否符合進場條件 ----
    before = bars_t[at(bars_t, p.touch_before)]
    if before.empty or before["high"].max() < lim_t.up:
        return None
    upto = bars_t[at(bars_t, p.check_at)]
    if upto.empty or upto["close"].iloc[-1] < lim_t.up:
        return None
    auction = bars_t.iloc[-1]  # 收盤集合競價
    close_t = float(auction["close"])
    if p.fill_mode == "opened" and close_t >= lim_t.up:
        return None
    t1 = bars_t1.reset_index(drop=True)
    open_t1 = float(t1.at[0, "open"])
    if p.fill_mode == "worst" and open_t1 >= close_t:
        return None
    entry = min(close_t, lim_t.up)  # 限價買單在集合競價以競價價格成交，不加滑價
    tr = Trade(code, LONG, auction["ts"], entry, info={"close_at_limit": close_t >= lim_t.up})

    # ---- T+1 ----
    def sell(i: int, px: float, frac, reason: str) -> None:
        tr.exit(t1.at[i, "ts"], slip(px, LONG, False, p.slippage_ticks, lim_t1), frac, reason)

    if open_t1 <= close_t:
        sell(0, open_t1, None, "gap_down")
        return tr
    sell(0, open_t1, p.open_sell_frac, "open")
    if tr.remaining <= 1e-9:
        return tr
    times = t1["ts"].dt.time
    for i in range(len(t1)):
        if times[i] >= p.exit_by:
            sell(i, t1.at[i, "open"], None, "time")
            break
        if t1.at[i, "close"] < open_t1 and i + 1 < len(t1):
            sell(i + 1, t1.at[i + 1, "open"], None, "break_open")
            break
    if tr.remaining > 1e-9:
        sell(len(t1) - 1, t1.at[len(t1) - 1, "close"], None, "eod")
    return tr
