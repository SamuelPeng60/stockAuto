"""1 分 K 本地儲存：data/minute/<code>/<yyyymm>.parquet，欄位見 st.backtest.intraday。

下載器（Shioaji kbars）尚未實作；任何來源只要轉成此格式寫入即可。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from .fetch import DATA_DIR

MINUTE_DIR = DATA_DIR / "minute"
BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount"]


class BarStore:
    def __init__(self, root: Path = MINUTE_DIR):
        self.root = root
        self._month = lru_cache(maxsize=256)(self._load_month)

    def _load_month(self, code: str, yyyymm: str) -> pd.DataFrame | None:
        path = self.root / code / f"{yyyymm}.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        df["ts"] = pd.to_datetime(df["ts"])
        return df.sort_values("ts").reset_index(drop=True)

    def get(self, code: str, day: pd.Timestamp) -> pd.DataFrame | None:
        m = self._month(code, f"{day:%Y%m}")
        if m is None:
            return None
        d = m[m["ts"].dt.normalize() == pd.Timestamp(day).normalize()]
        return d.reset_index(drop=True) if len(d) else None

    def put(self, code: str, bars: pd.DataFrame) -> None:
        """寫入（與既有月份檔合併、以 ts 去重）。"""
        bars = bars.reindex(columns=BAR_COLUMNS).copy()
        bars["ts"] = pd.to_datetime(bars["ts"])
        for ym, part in bars.groupby(bars["ts"].dt.strftime("%Y%m")):
            path = self.root / code / f"{ym}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                part = pd.concat([pd.read_parquet(path), part]).drop_duplicates("ts", keep="last")
            part.sort_values("ts").to_parquet(path, index=False)
        self._month.cache_clear()
