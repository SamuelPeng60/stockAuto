"""報告要求必須分段檢視的市場期間（以 T+1 交易日歸屬）。"""

from __future__ import annotations

import pandas as pd

SEGMENTS: dict[str, tuple[str, str]] = {
    "論文樣本 2017/4/28–2020/12/31": ("2017-04-28", "2020-12-31"),
    "逐筆交易前 –2020/3/20": ("2017-04-28", "2020-03-20"),
    "逐筆交易後 2020/3/23–": ("2020-03-23", "2099-12-31"),
    "樣本外 2021–": ("2021-01-01", "2099-12-31"),
    "熱絡盤 2020/3/23–2021": ("2020-03-23", "2021-12-31"),
    "空頭 2022": ("2022-01-01", "2022-12-31"),
    "AI 多頭 2023–2024": ("2023-01-01", "2024-12-31"),
    "關稅崩跌 2025/4–5（含限空期）": ("2025-04-01", "2025-05-31"),
    "急跌 2026/7": ("2026-07-01", "2026-07-31"),
    "處置新制前 2026/1/1–8/7": ("2026-01-01", "2026-08-07"),
    "處置新制後 2026/8/10–": ("2026-08-10", "2099-12-31"),
}


def split(trades: pd.DataFrame, date_col: str = "trade_date") -> dict[str, pd.DataFrame]:
    out = {"全部": trades}
    for name, (a, b) in SEGMENTS.items():
        out[name] = trades[trades[date_col].between(pd.Timestamp(a), pd.Timestamp(b))]
    for y, g in trades.groupby(trades[date_col].dt.year):
        out[f"{y} 年"] = g
    return out
