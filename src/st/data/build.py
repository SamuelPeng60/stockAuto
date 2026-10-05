"""把原始 JSON 組成單一日 K 表 data/daily.parquet。

欄位：date, market, code, name, kind, open, high, low, close, change, volume, value, trades,
      ref, limit_up, limit_down
- 上市：ref／limit_up／limit_down 取自 TWT84U（當日官方值）
- 上櫃：取自「前一交易日」行情表的次日參考價／漲跌停價
- 全部為原始（未還原）價格
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from st.core.calendar import prev_day_map
from st.core.prices import limit_down, limit_up

from .fetch import DATA_DIR, RAW_DIR
from .parse import parse_tpex_quotes, parse_twse_limits, parse_twse_quotes

log = logging.getLogger(__name__)

DAILY_PATH = DATA_DIR / "daily.parquet"
COLUMNS = ["date", "market", "code", "name", "kind", "open", "high", "low", "close", "change",
           "volume", "value", "trades", "ref", "limit_up", "limit_down", "limit_src"]


def _load_all(source: str, parser: Callable[[dict, date], pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for path in sorted((RAW_DIR / source).glob("*/*.json")):
        d = datetime.strptime(path.stem, "%Y%m%d").date()
        df = parser(json.loads(path.read_text(encoding="utf-8"), strict=False), d)
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_twse() -> pd.DataFrame:
    quotes = _load_all("twse_quotes", parse_twse_quotes)
    if quotes.empty:
        return quotes
    limits = _load_all("twse_limits", parse_twse_limits)
    if limits.empty:
        limits = pd.DataFrame(columns=["date", "code", "ref", "limit_up", "limit_down"])
    df = quotes.merge(limits, on=["date", "code"], how="left")
    df["limit_src"] = df["limit_up"].notna().map({True: "official", False: None})

    # 沒有官方值的日子：參考價 = 收盤價 − 漲跌價差（除權息日的漲跌也是相對參考價，所以成立），
    # 再自算漲跌停價。已知少數情況（約 0.2%，疑似除權息日）與官方值不同；
    # 不比價（漲跌為 X）或當日無成交者算不出來，保持 NaN。
    calc = df["ref"].isna() & df["close"].notna() & df["change"].notna()
    ref = (df.loc[calc, "close"] - df.loc[calc, "change"]).round(2)
    etf = df.loc[calc, "kind"].eq("etf")
    df.loc[calc, "ref"] = ref
    df.loc[calc, "limit_up"] = [float(limit_up(Decimal(f"{r:.2f}"), e)) for r, e in zip(ref, etf)]
    df.loc[calc, "limit_down"] = [float(limit_down(Decimal(f"{r:.2f}"), e)) for r, e in zip(ref, etf)]
    df.loc[calc, "limit_src"] = "calc"
    n_calc = int(calc.sum())
    if n_calc:
        log.info("上市：%d 列用自算漲跌停價（%.1f%%）", n_calc, 100 * n_calc / len(df))
    df["market"] = "TWSE"
    return df


def build_tpex() -> pd.DataFrame:
    # 兩個來源：tpex_quotes（含權證的完整版）與 tpex_lite（不含權證）。同一天兩者都有時用完整版
    full = _load_all("tpex_quotes", parse_tpex_quotes)
    lite = _load_all("tpex_lite", parse_tpex_quotes)
    if not full.empty and not lite.empty:
        lite = lite[~lite["date"].isin(full["date"].unique())]
    parts = [p for p in (full, lite) if not p.empty]
    if not parts:
        return pd.DataFrame()
    q = pd.concat(parts, ignore_index=True)
    # 前一交易日；跨資料缺口不配對，否則會把幾年前的「次日漲跌停價」套到缺口後第一天
    q["prev_date"] = q["date"].map(prev_day_map(q["date"]))
    nxt = q[["date", "code", "next_ref", "next_limit_up", "next_limit_down"]].rename(
        columns={"date": "prev_date", "next_ref": "ref",
                 "next_limit_up": "limit_up", "next_limit_down": "limit_down"})
    df = q.merge(nxt, on=["prev_date", "code"], how="left")
    df["limit_src"] = df["limit_up"].notna().map({True: "official", False: None})
    # tpex_lite 沒有次日參考價：用「收盤 − 漲跌」推當日參考價（漲跌停價仍是前一日的官方值）
    calc = df["ref"].isna() & df["limit_up"].notna() & df["close"].notna() & df["change"].notna()
    df.loc[calc, "ref"] = (df.loc[calc, "close"] - df.loc[calc, "change"]).round(2)
    df["market"] = "TPEX"
    return df


def build(path: Path = DAILY_PATH) -> pd.DataFrame:
    parts = [p for p in (build_twse(), build_tpex()) if not p.empty]
    if not parts:
        raise RuntimeError("沒有任何原始資料，請先執行 fetch")
    df = pd.concat([p.reindex(columns=COLUMNS) for p in parts], ignore_index=True)
    # 無漲跌幅限制的標的（境外 ETF、新上市前 5 日等），官方以 9999.95／0.01 表示
    no_limit = (df["limit_up"] >= 9999) | (df["limit_down"] <= 0.01)
    df.loc[no_limit, ["limit_up", "limit_down"]] = float("nan")
    df = df.sort_values(["date", "market", "code"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("寫入 %s：%d 列，%s ~ %s", path, len(df), df["date"].min().date(), df["date"].max().date())
    return df


def load_daily(path: Path = DAILY_PATH) -> pd.DataFrame:
    return pd.read_parquet(path)
