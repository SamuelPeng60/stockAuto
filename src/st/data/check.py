"""資料品質檢查：自算漲跌停價 vs 官方值、價格欄位合理性。"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from st.core.prices import limit_down, limit_up


def check_limits(df: pd.DataFrame) -> pd.DataFrame:
    """回傳自算與官方漲跌停價不一致的列。"""
    d = df[df["limit_src"].eq("official")].dropna(subset=["ref", "limit_up", "limit_down"])
    d = d[d["ref"] > 0]
    etf = d["kind"].eq("etf")
    up = [float(limit_up(Decimal(str(r)), e)) for r, e in zip(d["ref"], etf)]
    dn = [float(limit_down(Decimal(str(r)), e)) for r, e in zip(d["ref"], etf)]
    d = d.assign(calc_up=up, calc_down=dn)
    bad = (d["calc_up"].round(2) != d["limit_up"].round(2)) | (d["calc_down"].round(2) != d["limit_down"].round(2))
    return d[bad]


def summary(df: pd.DataFrame) -> str:
    lines = [f"總列數 {len(df):,}，交易日 {df['date'].nunique():,}，代號 {df['code'].nunique():,}"]
    for (m, k), g in df.groupby(["market", "kind"]):
        src = g["limit_src"].value_counts().to_dict()
        lines.append(f"  {m} {k}: {len(g):,} 列，漲跌停價缺值 {g['limit_up'].isna().mean():.2%}，來源 {src}")
    bad = check_limits(df)
    has = df[df["limit_src"].eq("official")].dropna(subset=["limit_up"])
    lines.append(f"自算 vs 官方漲跌停價不一致：{len(bad):,} / {len(has):,}")
    for k, g in bad.groupby("kind"):
        lines.append(f"  {k}: {len(g):,} 列，例：")
        lines.append(g[["date", "market", "code", "name", "ref", "limit_up", "calc_up", "limit_down", "calc_down"]]
                     .head(5).to_string(index=False))
    traded = df.dropna(subset=["close"])
    ohlc_bad = traded[(traded["high"] < traded[["open", "close", "low"]].max(axis=1))
                      | (traded["low"] > traded[["open", "close", "high"]].min(axis=1))]
    lines.append(f"OHLC 不合理：{len(ohlc_bad):,} 列")
    return "\n".join(lines)
