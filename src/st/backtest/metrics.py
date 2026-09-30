"""回測績效指標（報告建議的全套）：勝率、平均淨報酬、盈虧比、Profit Factor、MDD、t 值、月度正報酬比例。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def equity_curve(trades: pd.DataFrame, ret_col: str = "net_ret", date_col: str = "trade_date") -> pd.Series:
    """每個交易日的所有部位等權重，日報酬取平均後複利累積。"""
    daily = trades.groupby(date_col)[ret_col].mean().sort_index()
    return (1 + daily).cumprod()


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    return float((equity / equity.cummax() - 1).min())


def summarize(trades: pd.DataFrame, ret_col: str = "net_ret", date_col: str = "trade_date") -> dict:
    r = trades[ret_col].dropna()
    n = len(r)
    if n == 0:
        return {"筆數": 0}
    wins, losses = r[r > 0], r[r <= 0]
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = -losses.mean() if len(losses) else 0.0
    std = r.std(ddof=1) if n > 1 else float("nan")
    monthly = trades.groupby(trades[date_col].dt.to_period("M"))[ret_col].sum()
    return {
        "筆數": n,
        "勝率": len(wins) / n,
        "平均淨報酬": r.mean(),
        "中位數": r.median(),
        "盈虧比": avg_win / avg_loss if avg_loss > 0 else float("inf"),
        "ProfitFactor": wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf"),
        "t值": r.mean() / (std / math.sqrt(n)) if n > 1 and std > 0 else float("nan"),
        "MDD": max_drawdown(equity_curve(trades, ret_col, date_col)),
        "月正報酬比例": float((monthly > 0).mean()),
        "月數": len(monthly),
    }


def format_table(rows: dict[str, dict]) -> str:
    """{區段名稱: summarize() 結果} → 文字表格。"""
    fmt = {"勝率": "{:.1%}", "平均淨報酬": "{:.3%}", "中位數": "{:.3%}", "MDD": "{:.1%}",
           "月正報酬比例": "{:.1%}", "筆數": "{:,.0f}", "月數": "{:,.0f}"}

    def cell(c: str, v) -> str:
        if pd.isna(v):
            return "-"
        if not np.isfinite(v):
            return "inf"
        return fmt.get(c, "{:.2f}").format(v)

    df = pd.DataFrame(rows).T
    if df.empty:
        return "(無資料)"
    return df.apply(lambda col: col.map(lambda v: cell(col.name, v))).to_string()
