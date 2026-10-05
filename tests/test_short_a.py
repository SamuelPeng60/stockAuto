import pandas as pd
import pytest

from st.backtest.metrics import max_drawdown, summarize
from st.core.costs import CostModel
from st.strategies.short_a import Params, build_trades

DAYS = pd.bdate_range("2021-03-01", periods=13)


def bar(i, code, o, h, l, c, *, lu=None, ld=None, vol=1000, market="TWSE", kind="stock"):
    ref = 100.0
    return dict(date=DAYS[i], market=market, code=code, name=code, kind=kind, open=o, high=h, low=l,
                close=c, change=0.0, volume=vol, value=vol * c, trades=1, ref=ref,
                limit_up=lu if lu is not None else 110.0, limit_down=ld if ld is not None else 90.0)


@pytest.fixture
def daily():
    rows = []
    # A：盤整 10 天 → D10 收盤漲停（量創新高）→ D11 開 108 收 104
    for i in range(10):
        rows.append(bar(i, "1101", 100, 101, 99, 100))
    rows.append(bar(10, "1101", 101, 110, 101, 110, vol=5000))
    rows.append(bar(11, "1101", 108, 109, 103, 104, lu=121.0, ld=99.0))
    # B：D10 收盤漲停，但 D11 暫停交易、D12 才有資料 → 不算事件
    for i in range(10):
        rows.append(bar(i, "2202", 100, 130, 99, 100))  # 振幅大，不算盤整
    rows.append(bar(10, "2202", 101, 110, 101, 110))
    rows.append(bar(12, "2202", 108, 109, 103, 104))
    # C：上櫃、D：ETF → 基準設定只做上市普通股
    for code, market, kind in (("6488", "TPEX", "stock"), ("0050", "TWSE", "etf")):
        rows.append(bar(10, code, 101, 110, 101, 110, market=market, kind=kind))
        rows.append(bar(11, code, 108, 109, 103, 104, market=market, kind=kind))
    # E：D11 開盤漲停再漲停收（買不回）
    rows.append(bar(10, "3303", 101, 110, 101, 110))
    rows.append(bar(11, "3303", 121, 121, 118, 121, lu=121.0, ld=99.0))
    # 讓 D12 成為交易日
    rows.append(bar(12, "9999", 100, 100, 100, 100))
    return pd.DataFrame(rows)


def test_events_and_returns(daily):
    p = Params(cost=CostModel(discount=1.0, min_fee=20, slippage_ticks=0))
    t = build_trades(daily, p).set_index("code")
    assert set(t.index) == {"1101", "3303"}
    a = t.loc["1101"]
    assert a.trade_date == DAYS[11]
    assert a.gross_ret == pytest.approx((108 - 104) / 108)
    assert a.net_ret == pytest.approx((108 - 104) / 108 - 0.00435)
    assert a.f_consolidation and a.f_volume_high
    assert t.loc["3303", "close_at_limit_up"] and not a.close_at_limit_up


def test_slippage(daily):
    p = Params(cost=CostModel(discount=1.0, min_fee=20, slippage_ticks=1))
    a = build_trades(daily, p).set_index("code").loc["1101"]
    assert (a.sell_px, a.buy_px) == (107.5, 104.5)  # 100–500 元檔位 0.5
    # 買回價不能超過漲停價
    e = build_trades(daily, p).set_index("code").loc["3303"]
    assert e.buy_px == 121.0


def test_min_gap_only_shorts_gap_up(daily):
    c0 = CostModel(discount=1.0, min_fee=20, slippage_ticks=0)
    t = build_trades(daily, Params(cost=c0)).set_index("code")
    assert t.loc["1101", "gap"] == pytest.approx(108 / 110 - 1)  # 開低
    assert t.loc["3303", "gap"] == pytest.approx(0.1) and t.loc["3303", "open_at_limit_up"]
    assert set(build_trades(daily, Params(cost=c0, min_gap=0.0))["code"]) == {"3303"}


def test_daily_stop(daily):
    c0 = CostModel(discount=1.0, min_fee=20, slippage_ticks=0)
    # 1101：開 108、高 109。停損 0.5% → 108.54 進位到 109.0，觸發，以 109 回補
    a = build_trades(daily, Params(cost=c0, stop_pct=0.005)).set_index("code").loc["1101"]
    assert a.stopped and a.buy_px == 109.0
    assert a.gross_ret == pytest.approx((108 - 109) / 108)
    # 停損 3% → 111.5，沒碰到，收盤 104 回補
    a = build_trades(daily, Params(cost=c0, stop_pct=0.03)).set_index("code").loc["1101"]
    assert not a.stopped and a.buy_px == 104.0
    # 3303 開在漲停 121：停損價高於漲停價，永遠不會觸發
    e = build_trades(daily, Params(cost=c0, stop_pct=0.03)).set_index("code").loc["3303"]
    assert not e.stopped and e.buy_px == 121.0
    # 滑價加在停損價之上
    c1 = CostModel(discount=1.0, min_fee=20, slippage_ticks=1)
    assert build_trades(daily, Params(cost=c1, stop_pct=0.005)).set_index("code").loc["1101", "buy_px"] == 109.5


def test_markets_param(daily):
    t = build_trades(daily, Params(markets=("TWSE", "TPEX")))
    assert "6488" in set(t["code"])


def test_metrics():
    t = pd.DataFrame({"trade_date": pd.to_datetime(["2021-01-04", "2021-01-05", "2021-02-01", "2021-02-02"]),
                      "net_ret": [0.01, -0.005, 0.02, -0.01]})
    s = summarize(t)
    assert s["筆數"] == 4 and s["勝率"] == 0.5
    assert s["盈虧比"] == pytest.approx(0.015 / 0.0075)
    assert s["ProfitFactor"] == pytest.approx(0.03 / 0.015)
    assert s["月正報酬比例"] == 1.0
    assert max_drawdown(pd.Series([1.0, 1.2, 0.9, 1.3])) == pytest.approx(0.9 / 1.2 - 1)
