"""1 分 K 盤中模擬的共用工具。

分 K 格式（每個 code、每天一個 DataFrame，依時間排序）：
  ts（Timestamp，K 棒「開始」時間，09:00 那根含開盤集合競價，13:30 那根是收盤集合競價）,
  open, high, low, close, volume, amount（成交金額，可缺；缺時用典型價 × 量估 VWAP）

成交假設（保守）：
- 用 K 棒收盤判斷的訊號，在「下一根」K 棒開盤成交
- 停損／停利價位：K 棒開盤已越過 → 以開盤價成交（跳空）；K 棒內觸及 → 以該價位成交
- 同一根 K 棒同時觸及停損與停利 → 視為先停損
- 所有成交價再往不利方向加 N 檔滑價，但不超出當日漲跌停價
- 尚未模擬瞬間價格穩定措施（暫緩撮合 2 分鐘）；可用滑價 2 檔做壓力測試
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal

import numpy as np
import pandas as pd

from st.core.costs import CostModel
from st.core.prices import shift_ticks

LONG, SHORT = 1, -1


# ---------- 指標 ----------

def at(bars: pd.DataFrame, t: time) -> pd.Series:
    """布林遮罩：K 棒開始時間 < t。"""
    return bars["ts"].dt.time < t


def vwap(bars: pd.DataFrame) -> pd.Series:
    """當日累積 VWAP（每根 K 棒收盤時的值）。"""
    vol = bars["volume"].astype(float)
    if "amount" in bars and bars["amount"].notna().all():
        amt = bars["amount"].astype(float)
    else:
        amt = (bars["high"] + bars["low"] + bars["close"]) / 3 * vol
    cum_vol = vol.cumsum().replace(0, np.nan)
    return (amt.cumsum() / cum_vol).ffill().fillna(bars["close"])


def opening_range(bars: pd.DataFrame, minutes: int = 15) -> tuple[float, float]:
    """09:00 起前 N 分鐘的最高與最低價。"""
    end = (pd.Timestamp("09:00") + pd.Timedelta(minutes=minutes)).time()
    r = bars[at(bars, end)]
    return float(r["high"].max()), float(r["low"].min())


def atr_5min(bars: pd.DataFrame, n: int = 14) -> float:
    """以 5 分 K 計算的 ATR（簡單平均 True Range）。bars 可以跨日（前一日＋當日到目前為止）。"""
    k = (bars.set_index("ts")
         .resample("5min", label="left", closed="left")
         .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
         .dropna())
    prev_close = k["close"].shift(1)
    tr = pd.concat([k["high"] - k["low"],
                    (k["high"] - prev_close).abs(),
                    (k["low"] - prev_close).abs()], axis=1).max(axis=1)
    tr = tr.iloc[1:]  # 第一根沒有前收
    return float(tr.tail(n).mean()) if len(tr) >= n else float("nan")


# ---------- 成交與部位 ----------

@dataclass(frozen=True)
class Limits:
    up: float
    down: float
    etf: bool = False


def slip(price: float, side: int, is_entry: bool, ticks: int, lim: Limits) -> float:
    """side：部位方向。進場買（多）或出場買（空）時價格往上滑，反之往下。"""
    buying = (side == LONG) == is_entry
    p = float(shift_ticks(Decimal(str(price)), ticks if buying else -ticks, lim.etf)) if ticks else price
    return min(p, lim.up) if buying else max(p, lim.down)


def stop_fill(bar: pd.Series, side: int, level: float) -> float | None:
    """停損：多單價格跌破 level、空單漲破 level 時的成交價；沒觸發回傳 None。"""
    if side == LONG:
        if bar["open"] <= level:
            return float(bar["open"])
        return level if bar["low"] <= level else None
    if bar["open"] >= level:
        return float(bar["open"])
    return level if bar["high"] >= level else None


def target_fill(bar: pd.Series, side: int, level: float) -> float | None:
    """停利：多單漲到 level、空單跌到 level。"""
    return stop_fill(bar, -side, level)


@dataclass
class Exit:
    ts: pd.Timestamp
    price: float
    frac: float
    reason: str


@dataclass
class Trade:
    code: str
    side: int
    entry_ts: pd.Timestamp
    entry_px: float
    exits: list[Exit] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return 1.0 - sum(e.frac for e in self.exits)

    def exit(self, ts, price: float, frac: float | None, reason: str) -> None:
        frac = self.remaining if frac is None else min(frac, self.remaining)
        if frac > 1e-9:
            self.exits.append(Exit(ts, float(price), frac, reason))

    def gross_ret(self) -> float:
        return sum(e.frac * self.side * (e.price - self.entry_px) / self.entry_px for e in self.exits)

    def to_row(self, cost: CostModel, daytrade: bool) -> dict:
        assert self.remaining < 1e-9, f"{self.code} 部位未完全出場"
        g = self.gross_ret()
        return {
            "code": self.code,
            "side": "long" if self.side == LONG else "short",
            "entry_ts": self.entry_ts,
            "trade_date": pd.Timestamp(self.entry_ts.date()),
            "entry_px": self.entry_px,
            "exit_px_avg": sum(e.frac * e.price for e in self.exits),
            "exit_reasons": "+".join(f"{e.reason}:{e.frac:g}" for e in self.exits),
            "last_exit_ts": self.exits[-1].ts,
            "gross_ret": g,  # 已含滑價
            "net_ret": g - cost.round_trip_pct(daytrade),
            **self.info,
        }


def bar_at_or_after(bars: pd.DataFrame, t: time) -> pd.Series | None:
    """第一根開始時間 >= t 的 K 棒。"""
    m = bars[bars["ts"].dt.time >= t]
    return m.iloc[0] if len(m) else None
