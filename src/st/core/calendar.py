"""由資料推導的交易日曆。相鄰兩日相隔超過 MAX_GAP_DAYS 視為資料缺口（不是連假），不互相對應。"""

from __future__ import annotations

import pandas as pd

MAX_GAP_DAYS = 15  # 台股最長休市（春節）約 10 天


def _cal(dates) -> pd.Series:
    cal = pd.DatetimeIndex(sorted(pd.unique(pd.Series(dates))))
    return pd.Series(cal, index=cal)


def next_day_map(dates) -> pd.Series:
    s = _cal(dates)
    nxt = s.shift(-1)
    return nxt.where((nxt - s).dt.days <= MAX_GAP_DAYS)


def prev_day_map(dates) -> pd.Series:
    s = _cal(dates)
    prv = s.shift(1)
    return prv.where((s - prv).dt.days <= MAX_GAP_DAYS)
