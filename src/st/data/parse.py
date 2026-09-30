"""把證交所／櫃買中心的原始 JSON 轉成 DataFrame。只做欄位對應與數字清理，不做跨日運算。"""

from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

STOCK_RE = re.compile(r"^[1-9]\d{3}$")  # 普通股：4 碼、非 0 開頭
ETF_RE = re.compile(r"^00\d{2,4}[A-Z]?$")  # ETF：00 開頭


def kind_of(code: str) -> str | None:
    if STOCK_RE.match(code):
        return "stock"
    if ETF_RE.match(code):
        return "etf"
    return None  # 權證、特別股、TDR 等不收


def num(s) -> float:
    """'1,234.50' → 1234.5；'--'、'---'、''、'除息' 等 → NaN。"""
    if s is None:
        return np.nan
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def _twse_sign(html: str) -> float:
    if "+" in html:
        return 1.0
    if "-" in html:
        return -1.0
    if "X" in html:  # 不比價
        return np.nan
    return 0.0


def _norm(field: str) -> str:
    return re.sub(r"\s+", "", field)


def _frame(rows: list[dict], d: date) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["kind"] = df["code"].map(kind_of)
    df = df[df["kind"].notna()].copy()
    df.insert(0, "date", pd.Timestamp(d))
    return df


def parse_twse_quotes(raw: dict, d: date) -> pd.DataFrame:
    if raw.get("stat") != "OK":
        return pd.DataFrame()
    table = next(t for t in raw["tables"] if "證券代號" in (t.get("fields") or []))
    f = {name: i for i, name in enumerate(table["fields"])}
    rows = []
    for r in table["data"]:
        sign = _twse_sign(r[f["漲跌(+/-)"]])
        rows.append(
            {
                "code": r[f["證券代號"]].strip(),
                "name": r[f["證券名稱"]].strip(),
                "open": num(r[f["開盤價"]]),
                "high": num(r[f["最高價"]]),
                "low": num(r[f["最低價"]]),
                "close": num(r[f["收盤價"]]),
                "change": sign * num(r[f["漲跌價差"]]),
                "volume": num(r[f["成交股數"]]),
                "value": num(r[f["成交金額"]]),
                "trades": num(r[f["成交筆數"]]),
            }
        )
    return _frame(rows, d)


def parse_twse_limits(raw: dict, d: date) -> pd.DataFrame:
    """TWT84U 欄位：證券代號、證券名稱、漲停價、開盤競價基準（參考價）、跌停價、…（有兩欄同名，用位置取）。"""
    if raw.get("stat") != "OK":
        return pd.DataFrame()
    assert raw["fields"][:5] == ["證券代號", "證券名稱", "漲停價", "開盤競價基準", "跌停價"], raw["fields"]
    rows = [
        {"code": r[0].strip(), "limit_up": num(r[2]), "ref": num(r[3]), "limit_down": num(r[4])}
        for r in raw["data"]
    ]
    return _frame(rows, d).drop(columns=["kind"], errors="ignore")


def parse_tpex_quotes(raw: dict, d: date) -> pd.DataFrame:
    """上櫃行情。注意參考價與漲跌停價欄位是「次日」的，要由 build 移到下一個交易日。"""
    tables = raw.get("tables") or []
    if not tables or not tables[0].get("data"):
        return pd.DataFrame()
    table = tables[0]
    f = {_norm(name): i for i, name in enumerate(table["fields"])}
    rows = []
    for r in table["data"]:
        rows.append(
            {
                "code": r[f["代號"]].strip(),
                "name": r[f["名稱"]].strip(),
                "open": num(r[f["開盤"]]),
                "high": num(r[f["最高"]]),
                "low": num(r[f["最低"]]),
                "close": num(r[f["收盤"]]),
                "change": num(r[f["漲跌"]]),
                "volume": num(r[f["成交股數"]]),
                "value": num(r[f["成交金額(元)"]]),
                "trades": num(r[f["成交筆數"]]),
                "next_ref": num(r[f["次日參考價"]]),
                "next_limit_up": num(r[f["次日漲停價"]]),
                "next_limit_down": num(r[f["次日跌停價"]]),
            }
        )
    return _frame(rows, d)
