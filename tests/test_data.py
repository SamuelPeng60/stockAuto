import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import st.data.build as build_mod
from st.data.parse import kind_of, num, parse_tpex_quotes, parse_twse_limits, parse_twse_quotes

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"), strict=False)


def test_num_and_kind():
    assert num("1,234.50") == 1234.5
    assert pd.isna(num("--")) and pd.isna(num("除息")) and pd.isna(num(""))
    assert num("-0.18 ") == -0.18
    assert kind_of("2330") == "stock" and kind_of("00878") == "etf" and kind_of("00625K") == "etf"
    assert kind_of("2881A") is None and kind_of("030001") is None


def test_parse_twse_quotes():
    df = parse_twse_quotes(load("twse_quotes_20250902.json"), date(2025, 9, 2)).set_index("code")
    tsmc = df.loc["2330"]
    assert (tsmc.open, tsmc.high, tsmc.low, tsmc.close) == (1170, 1175, 1155, 1160)
    assert tsmc.change == -5 and tsmc.volume == 14_994_018 and tsmc.kind == "stock"
    assert df.loc["0050", "change"] == 0


def test_parse_twse_limits():
    df = parse_twse_limits(load("twse_limits_20250902.json"), date(2025, 9, 2)).set_index("code")
    assert tuple(df.loc["2330", ["limit_up", "ref", "limit_down"]]) == (1280, 1165, 1050)


def test_parse_tpex_quotes():
    df = parse_tpex_quotes(load("tpex_quotes_20250901.json"), date(2025, 9, 1)).set_index("code")
    r = df.loc["6488"]
    assert r.close == 364.5 and r.change == -7.5
    assert (r.next_ref, r.next_limit_up, r.next_limit_down) == (364.5, 400.5, 328.5)


def test_holiday_is_empty():
    assert parse_twse_quotes({"stat": "很抱歉，沒有符合條件的資料!"}, date(2025, 10, 10)).empty
    assert parse_tpex_quotes({"tables": [{"data": []}]}, date(2025, 10, 10)).empty


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    for f in FIX.glob("*.json"):
        source, day = f.stem.rsplit("_", 1)
        dest = tmp_path / source / day[:4] / f"{day}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)
    monkeypatch.setattr(build_mod, "RAW_DIR", tmp_path)
    return tmp_path


def test_build(raw_dir, tmp_path):
    df = build_mod.build(tmp_path / "daily.parquet")
    row = lambda d, c: df[(df.date == d) & (df.code == c)].iloc[0]
    # 上市：當日官方漲跌停價
    tsmc = row("2025-09-02", "2330")
    assert (tsmc.market, tsmc.ref, tsmc.limit_up, tsmc.limit_down) == ("TWSE", 1165, 1280, 1050)
    # 上櫃：09/02 的漲跌停價來自 09/01 行情表的「次日」欄位
    gw = row("2025-09-02", "6488")
    assert (gw.market, gw.ref, gw.limit_up, gw.limit_down) == ("TPEX", 364.5, 400.5, 328.5)
    assert pd.isna(row("2025-09-01", "6488").limit_up)  # 沒有前一日資料
    # 無漲跌幅限制的境外 ETF → NaN
    assert pd.isna(row("2025-09-02", "0061").limit_up)
    assert tsmc.limit_src == "official"


def test_build_without_official_limits(raw_dir, tmp_path):
    shutil.rmtree(raw_dir / "twse_limits")
    df = build_mod.build(tmp_path / "daily.parquet")
    row = lambda d, c: df[(df.date == d) & (df.code == c)].iloc[0]
    # 參考價 = 收盤 1160 − 漲跌 (−5) = 1165，自算結果與官方一致
    tsmc = row("2025-09-02", "2330")
    assert (tsmc.ref, tsmc.limit_up, tsmc.limit_down, tsmc.limit_src) == (1165, 1280, 1050, "calc")
    etf = row("2025-09-02", "0050")  # 平盤：參考價 = 收盤 52
    assert (etf.ref, etf.limit_up, etf.limit_down) == (52, 57.2, 46.8)
