from datetime import time

import pandas as pd
import pytest

from st.backtest.intraday import (LONG, SHORT, Limits, atr_5min, opening_range, slip, stop_fill, vwap)
from st.data.minute import BarStore
from st.strategies import long_a, orb
from st.strategies.short_a import IntradayParams, simulate_intraday


def make_bars(day: str, base: float, overrides: dict[str, tuple] | None = None, vol: int = 1000,
              until: str = "13:24") -> pd.DataFrame:
    """整個交易時段每分鐘一根平盤 K 棒（價格 = base），再用 overrides {"HH:MM": (o, h, l, c)} 覆寫，
    最後加一根 13:30 收盤集合競價（預設沿用前一根收盤）。"""
    overrides = dict(overrides or {})
    ts = pd.date_range(f"{day} 09:00", f"{day} {until}", freq="1min")
    rows = []
    last = base
    for t in ts:
        o, h, l, c = overrides.get(t.strftime("%H:%M"), (last, last, last, last))
        rows.append(dict(ts=t, open=o, high=h, low=l, close=c, volume=vol, amount=None))
        last = c
    if until == "13:24":
        o, h, l, c = overrides.get("13:30", (last, last, last, last))
        rows.append(dict(ts=pd.Timestamp(f"{day} 13:30"), open=o, high=h, low=l, close=c, volume=vol, amount=None))
    return pd.DataFrame(rows)


def ramp(start: str, end: str, p0: float, p1: float) -> dict[str, tuple]:
    """start..end 每分鐘線性從 p0 走到 p1。"""
    ts = pd.date_range(f"2021-01-01 {start}", f"2021-01-01 {end}", freq="1min")
    out, prev = {}, p0
    for k, t in enumerate(ts):
        c = round(p0 + (p1 - p0) * (k + 1) / len(ts), 2)
        out[t.strftime("%H:%M")] = (prev, max(prev, c), min(prev, c), c)
        prev = c
    return out


NO_LIMIT = Limits(up=1e9, down=0.0)


# ---------- 工具 ----------

def test_vwap_and_opening_range():
    b = make_bars("2021-03-02", 100, {"09:00": (100, 102, 99, 101), "09:20": (101, 110, 101, 110)})
    assert opening_range(b, 15) == (102, 99)
    b.loc[:, "amount"] = b["close"] * b["volume"]
    v = vwap(b)
    assert v.iloc[0] == pytest.approx(101)
    assert v.iloc[1] == pytest.approx((101 + 101) / 2)


def test_atr_5min():
    # 每 5 分鐘高低差 2、無跳空 → ATR = 2
    ov = {}
    for k, t in enumerate(pd.date_range("2021-03-02 09:00", "2021-03-02 10:39", freq="1min")):
        ov[t.strftime("%H:%M")] = (100, 101, 99, 100)
    b = make_bars("2021-03-02", 100, ov, until="10:39")
    assert atr_5min(b, 14) == pytest.approx(2)
    assert atr_5min(b.head(20), 14) != atr_5min(b.head(20), 14)  # 不足 14 根 → NaN


def test_stop_fill_and_slip():
    bar = pd.Series({"open": 100.0, "high": 101.0, "low": 97.0, "close": 98.0})
    assert stop_fill(bar, LONG, 98.0) == 98.0  # 盤中觸及
    assert stop_fill(bar, LONG, 100.5) == 100.0  # 開盤已跌破 → 開盤價
    assert stop_fill(bar, LONG, 96.0) is None
    assert stop_fill(bar, SHORT, 101.0) == 101.0
    lim = Limits(up=110.0, down=90.0)
    assert slip(100.0, LONG, True, 1, lim) == 100.5  # 多單買進往上
    assert slip(100.0, SHORT, True, 1, lim) == 99.9  # 空單賣出往下（跨到 0.1 檔）
    assert slip(110.0, SHORT, False, 1, lim) == 110.0  # 回補不超過漲停


def test_bar_store_roundtrip(tmp_path):
    s = BarStore(tmp_path)
    b = make_bars("2021-03-02", 100)
    s.put("2330", b)
    s.put("2330", make_bars("2021-03-03", 101))
    got = s.get("2330", pd.Timestamp("2021-03-02"))
    assert len(got) == len(b) and got["close"].iloc[0] == 100
    assert s.get("2330", pd.Timestamp("2021-03-04")) is None
    assert s.get("9999", pd.Timestamp("2021-03-02")) is None


# ---------- ORB ----------

def orb_bars(after: dict[str, tuple]) -> pd.DataFrame:
    ov = {"09:00": (100, 102, 100, 101), "09:07": (101, 102, 100, 101),
          "09:15": (101, 103, 101, 103)}  # 09:15 收盤 103 突破區間高 102
    ov |= {"09:16": (103, 103.5, 103, 103.5)} | after
    return make_bars("2021-03-02", 101, ov)


def test_orb_long_tp_then_time_exit():
    bars = orb_bars(ramp("09:17", "09:40", 103.5, 110))
    p = orb.Params(side=LONG, slippage_ticks=0)
    tr = orb.simulate("2330", bars, None, NO_LIMIT, 100, p)
    assert tr.entry_px == 103  # 09:16 開盤
    assert tr.info["stop"] == 100  # 無 ATR → 區間低點
    assert tr.info["target"] == 109  # 103 + 2 × 3
    reasons = [(e.reason, e.frac) for e in tr.exits]
    assert reasons == [("tp", 0.5), ("time", 0.5)]
    assert tr.exits[0].price == 109 and tr.exits[1].ts.time() == time(13, 15)
    assert tr.gross_ret() == pytest.approx(0.5 * 6 / 103 + 0.5 * 7 / 103)


def test_orb_long_stop():
    bars = orb_bars({"09:30": (103, 103, 99.5, 99.5)})
    tr = orb.simulate("2330", bars, None, NO_LIMIT, 100, orb.Params(side=LONG, slippage_ticks=0))
    assert [e.reason for e in tr.exits] == ["stop"]
    assert tr.gross_ret() == pytest.approx((100 - 103) / 103)


def test_orb_long_skips_near_limit_up():
    bars = orb_bars({})
    lim = Limits(up=104.0, down=90.0)  # 103 距漲停 104 不到 2%
    assert orb.simulate("2330", bars, None, lim, 100, orb.Params(side=LONG)) is None


def test_orb_short_trailing_vwap_and_cooldown():
    ov = {"09:00": (100, 100, 98, 99), "09:07": (99, 100, 98, 99),
          "09:15": (99, 99, 97, 97)}  # 跌破區間低 98
    ov |= ramp("09:16", "09:30", 97, 90.5)  # 跌破 2R 目標 91
    ov |= ramp("09:31", "09:50", 90.5, 99)  # 反彈站上 VWAP（但未到停損 100）→ 其餘出場
    bars = make_bars("2021-03-02", 99, ov)
    p = orb.Params(side=SHORT, slippage_ticks=0)
    tr = orb.simulate("2330", bars, None, NO_LIMIT, 99, p)
    assert tr.entry_px == 97 and tr.info["stop"] == 100
    assert [e.reason for e in tr.exits] == ["tp", "vwap"]
    assert tr.exits[0].price == pytest.approx(91)
    assert tr.gross_ret() > 0
    # 前日收盤跌停 → 不得在平盤（99）以下放空
    assert orb.simulate("2330", bars, None, NO_LIMIT, 99, p, short_below_ref_ok=False) is None


# ---------- 做多 A 隔日沖 ----------

def lock_limit_day(close=110.0):
    ov = ramp("09:00", "09:59", 100, 109.5) | {"10:00": (109.5, 110, 109.5, 110)}
    ov["13:30"] = (close, close, close, close)
    return make_bars("2021-03-02", 110, ov)


def test_long_a_open_then_break_open():
    t1 = make_bars("2021-03-03", 114, {"09:00": (115, 115, 114, 114)})  # 開 115，第一根收 114 跌破開盤
    p = long_a.Params(slippage_ticks=0)
    tr = long_a.simulate("1101", lock_limit_day(), t1, Limits(110, 90), Limits(121, 99), p)
    assert tr.entry_px == 110
    assert [(e.reason, e.frac) for e in tr.exits] == [("open", 0.5), ("break_open", 0.5)]
    assert tr.gross_ret() == pytest.approx(0.5 * 5 / 110 + 0.5 * 4 / 110)


def test_long_a_gap_down_and_fill_modes():
    t1 = make_bars("2021-03-03", 108)
    tr = long_a.simulate("1101", lock_limit_day(), t1, Limits(110, 90), Limits(121, 99),
                         long_a.Params(slippage_ticks=0))
    assert [e.reason for e in tr.exits] == ["gap_down"]
    assert tr.gross_ret() == pytest.approx(-2 / 110)
    up = make_bars("2021-03-03", 115)
    # opened：收盤鎖死漲停 → 排不到
    assert long_a.simulate("1101", lock_limit_day(), up, Limits(110, 90), Limits(121, 99),
                           long_a.Params(fill_mode="opened")) is None
    # opened：收盤漲停被打開（109.5）→ 以 109.5 成交
    tr = long_a.simulate("1101", lock_limit_day(109.5), up, Limits(110, 90), Limits(121, 99),
                         long_a.Params(fill_mode="opened"))
    assert tr.entry_px == 109.5
    # worst：T+1 開高 → 不算成交
    assert long_a.simulate("1101", lock_limit_day(), up, Limits(110, 90), Limits(121, 99),
                           long_a.Params(fill_mode="worst")) is None


def test_long_a_requires_limit_at_1325():
    ov = ramp("09:00", "09:59", 100, 110) | {"13:24": (110, 110, 108, 108)}  # 13:25 前打開
    bars = make_bars("2021-03-02", 110, ov)
    assert long_a.simulate("1101", bars, make_bars("2021-03-03", 115), Limits(110, 90), Limits(121, 99)) is None


# ---------- 做空 A 盤中版 ----------

def test_short_a_open_with_stop():
    t1 = make_bars("2021-03-03", 105, {"10:00": (105, 108.5, 105, 108)})
    tr = simulate_intraday("1101", t1, NO_LIMIT, 110, IntradayParams(slippage_ticks=0))
    assert tr.entry_px == 105
    assert tr.info["stop"] == pytest.approx(108.15)  # min(105 × 1.03, 前日漲停 110)
    assert [e.reason for e in tr.exits] == ["stop"]
    assert tr.gross_ret() == pytest.approx((105 - 108.15) / 105)


def test_short_a_time_exit_and_close_exit():
    t1 = make_bars("2021-03-03", 105, ramp("09:00", "12:00", 105, 100))
    tr = simulate_intraday("1101", t1, NO_LIMIT, 110, IntradayParams(slippage_ticks=0))
    assert [e.reason for e in tr.exits] == ["time"] and tr.exits[0].price == 100
    tr = simulate_intraday("1101", t1, NO_LIMIT, 110, IntradayParams(slippage_ticks=0, exit_time=None))
    assert [e.reason for e in tr.exits] == ["close"]


def test_short_a_delayed_entry():
    t1 = make_bars("2021-03-03", 105, {"09:15": (105, 105, 104, 104)})
    tr = simulate_intraday("1101", t1, NO_LIMIT, 110, IntradayParams(entry="delayed", slippage_ticks=0))
    assert tr.entry_ts.time() == time(9, 16) and tr.entry_px == 104
    flat = make_bars("2021-03-03", 105)
    assert simulate_intraday("1101", flat, NO_LIMIT, 110, IntradayParams(entry="delayed")) is None
