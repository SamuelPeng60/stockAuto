"""做多 B／做空 B：開盤區間突破（ORB），加 VWAP 濾網與 ATR 停損。報告證據評級 D（個股），參數皆未驗證。

做多 B：前日漲幅 >5% 或收盤創 20 日新高、成交值 >5 億、流動性前 200 名；排除前日收漲停股（留給做空 A）
  09:15–11:00 間 1 分 K 收盤突破 09:00–09:15 區間高點且在 VWAP 之上 → 下一根開盤買進；
  距漲停 <2% 不進場。停損取「區間低點」與「進場價 − 1×ATR(14, 5 分 K)」較近者；
  2R 先出一半，其餘收盤跌破 VWAP 出場；13:15 前全部平倉。
做空 B：前日跌幅 >4% 或收盤創 20 日新低，其餘條件鏡像；13:20 前全部回補。
  前日收盤跌停者，當日不得在平盤以下融券／借券賣出（冷卻機制）→ 進場價低於參考價就放棄。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

from st.backtest.intraday import (LONG, SHORT, Limits, Trade, atr_5min, opening_range, slip, stop_fill,
                                  target_fill, vwap)
from st.core.calendar import next_day_map


@dataclass(frozen=True)
class Params:
    side: int = LONG
    or_minutes: int = 15
    entry_end: time = time(11, 0)
    exit_time: time | None = None  # None → 做多 13:15、做空 13:20
    atr_n: int = 14
    atr_mult: float = 1.0
    tp_r: float = 2.0
    tp_frac: float = 0.5
    limit_buffer: float = 0.02  # 距漲（跌）停 2% 以內不進場
    slippage_ticks: int = 1
    # 選股（前一交易日）
    prev_move: float = 0.05  # 做多：漲幅 > 5%；做空：跌幅 > 4% 請設 0.04
    lookback_extreme: int = 20
    min_value: float = 5e8
    liquidity_rank: int = 200
    liquidity_days: int = 20
    # 前日收漲停股屬於做空 A 的選股池，且證據顯示次日盤中偏弱 → 做多 B 排除，避免自己跟自己對作
    exclude_prev_limit_up: bool = False

    @property
    def exit_at(self) -> time:
        return self.exit_time or (time(13, 15) if self.side == LONG else time(13, 20))


LONG_B = Params(side=LONG, prev_move=0.05, exclude_prev_limit_up=True)
SHORT_B = Params(side=SHORT, prev_move=0.04)


def select_candidates(daily: pd.DataFrame, p: Params) -> pd.DataFrame:
    """以 T−1 日 K 選股，回傳 T 日（交易日）的候選清單與當日參考價、漲跌停價。"""
    d = daily[daily["kind"].eq("stock")].sort_values(["code", "date"]).copy()
    g = d.groupby("code", sort=False)
    d["pct"] = d["close"] / d["ref"] - 1
    n = p.lookback_extreme
    if p.side == LONG:
        extreme = d["close"] >= g["close"].transform(lambda s: s.rolling(n, min_periods=n).max())
        move = d["pct"] > p.prev_move
    else:
        extreme = d["close"] <= g["close"].transform(lambda s: s.rolling(n, min_periods=n).min())
        move = d["pct"] < -p.prev_move
    avg_value = g["value"].transform(lambda s: s.rolling(p.liquidity_days, min_periods=p.liquidity_days).mean())
    d["liq_rank"] = avg_value.groupby(d["date"]).rank(ascending=False)
    d["prev_close_limit_down"] = d["close"].round(2) == d["limit_down"].round(2)
    d["prev_close_limit_up"] = d["close"].round(2) == d["limit_up"].round(2)
    ok = (move | extreme) & (d["value"] > p.min_value) & (d["liq_rank"] <= p.liquidity_rank)
    if p.exclude_prev_limit_up:
        ok &= ~d["prev_close_limit_up"]
    sel = d[ok]

    # 對應到下一個交易日（該股當日必須有資料，否則視為暫停交易）
    sel = sel.assign(trade_date=sel["date"].map(next_day_map(d["date"]))).dropna(subset=["trade_date"])
    today = d[["date", "code", "ref", "limit_up", "limit_down", "market"]].rename(columns={"date": "trade_date"})
    out = sel[["trade_date", "code", "name", "pct", "value", "liq_rank", "prev_close_limit_down",
               "prev_close_limit_up"]].merge(
        today, on=["trade_date", "code"], how="inner")
    return out.dropna(subset=["limit_up", "limit_down", "ref"]).reset_index(drop=True)


def simulate(code: str, bars: pd.DataFrame, prev_bars: pd.DataFrame | None, lim: Limits, ref: float,
             p: Params, short_below_ref_ok: bool = True) -> Trade | None:
    side = p.side
    or_end = (pd.Timestamp("09:00") + pd.Timedelta(minutes=p.or_minutes)).time()
    or_hi, or_lo = opening_range(bars, p.or_minutes)
    v = vwap(bars).to_numpy()
    rows = bars.reset_index(drop=True)
    times = rows["ts"].dt.time

    # ---- 進場 ----
    entry_i = None
    for i in range(len(rows) - 1):
        t = times[i]
        if t < or_end:
            continue
        if t >= p.entry_end:
            break
        c = rows.at[i, "close"]
        if side == LONG:
            sig = c > or_hi and c > v[i] and c < lim.up * (1 - p.limit_buffer)
        else:
            sig = c < or_lo and c < v[i] and c > lim.down * (1 + p.limit_buffer)
        if sig:
            entry_i = i + 1
            break
    if entry_i is None or times[entry_i] >= p.exit_at:
        return None

    bar = rows.iloc[entry_i]
    entry = slip(bar["open"], side, True, p.slippage_ticks, lim)
    if side == SHORT and not short_below_ref_ok and entry < ref:
        return None

    hist = rows.iloc[:entry_i]
    if prev_bars is not None and len(prev_bars):
        hist = pd.concat([prev_bars, hist], ignore_index=True)
    atr = atr_5min(hist, p.atr_n)
    band = p.atr_mult * atr
    if side == LONG:
        stop = or_lo if atr != atr else max(or_lo, entry - band)
    else:
        stop = or_hi if atr != atr else min(or_hi, entry + band)
    risk = (entry - stop) * side
    if risk <= 0:
        return None
    target = entry + side * p.tp_r * risk

    tr = Trade(code, side, bar["ts"], entry,
               info={"or_hi": or_hi, "or_lo": or_lo, "atr": atr, "stop": stop, "target": target})

    # ---- 出場 ----
    half_taken = False
    trail_pending = False
    for j in range(entry_i, len(rows)):
        bar = rows.iloc[j]
        if times[j] >= p.exit_at:
            tr.exit(bar["ts"], slip(bar["open"], side, False, p.slippage_ticks, lim), None, "time")
            break
        if trail_pending:
            tr.exit(bar["ts"], slip(bar["open"], side, False, p.slippage_ticks, lim), None, "vwap")
            break
        px = stop_fill(bar, side, stop)
        if px is not None:
            tr.exit(bar["ts"], slip(px, side, False, p.slippage_ticks, lim), None, "stop")
            break
        if not half_taken:
            px = target_fill(bar, side, target)
            if px is not None:
                tr.exit(bar["ts"], slip(px, side, False, p.slippage_ticks, lim), p.tp_frac, "tp")
                half_taken = True
        if half_taken and (bar["close"] - v[j]) * side < 0:
            trail_pending = True
    if tr.remaining > 1e-9:  # 資料提早結束
        last = rows.iloc[-1]
        tr.exit(last["ts"], slip(last["close"], side, False, p.slippage_ticks, lim), None, "eod")
    return tr
