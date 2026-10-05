import pandas as pd
import pytest

from st.backtest.portfolio import block_opposite
from st.core.costs import CostModel
from st.strategies import orb, overnight

DAYS = pd.bdate_range("2021-03-01", periods=25)


def bar(i, code, o, h, l, c, *, lu=110.0, ld=90.0, vol=1000, value=None, market="TWSE", kind="stock"):
    return dict(date=DAYS[i], market=market, code=code, name=code, kind=kind, open=o, high=h, low=l,
                close=c, change=0.0, volume=vol, value=vol * c if value is None else value, trades=1,
                ref=100.0, limit_up=lu, limit_down=ld)


@pytest.fixture
def daily():
    rows = []
    # A：D0 收盤鎖漲停 110，D1 開 115 收 112
    rows += [bar(0, "1101", 101, 110, 101, 110), bar(1, "1101", 115, 116, 111, 112, lu=121.0, ld=99.0)]
    # B：D0 一字漲停，D1 開 108（開低）
    rows += [bar(0, "2202", 110, 110, 110, 110), bar(1, "2202", 108, 109, 107, 108, lu=121.0, ld=99.0)]
    # C：D0 觸及漲停但收 108（收盤打開），D1 開 107
    rows += [bar(0, "3303", 101, 110, 101, 108), bar(1, "3303", 107, 108, 106, 107, lu=118.5, ld=97.5)]
    # D：D0 收漲停，D1 沒交易 → 不算事件；E：沒碰漲停
    rows += [bar(0, "4404", 101, 110, 101, 110), bar(2, "4404", 115, 115, 115, 115)]
    rows += [bar(0, "5505", 100, 105, 100, 105), bar(1, "5505", 105, 105, 105, 105)]
    rows.append(bar(2, "9999", 100, 100, 100, 100))
    return pd.DataFrame(rows)


def test_overnight_events_and_returns(daily):
    p = overnight.Params(cost=CostModel(discount=1.0, min_fee=20, slippage_ticks=0))
    t = overnight.build_trades(daily, p).set_index("code")
    assert set(t.index) == {"1101", "2202", "3303"}
    a = t.loc["1101"]
    assert a.trade_date == DAYS[1] and a.close_at_limit and a.gap_up and not a.locked_all_day
    assert a.gross_ret == pytest.approx(115 / 110 - 1)
    assert a.net_ret == pytest.approx(115 / 110 - 1 - 0.00585)  # 非當沖：0.1425%×2 + 0.3%
    assert a.intraday_ret == pytest.approx(112 / 115 - 1)
    assert t.loc["2202", "locked_all_day"] and not t.loc["2202", "gap_up"]
    assert not t.loc["3303", "close_at_limit"]
    assert set(overnight.FILTERS["收盤鎖漲停，排除一字鎖"](t.reset_index())["code"]) == {"1101"}


def test_overnight_sell_slippage(daily):
    p = overnight.Params(cost=CostModel(discount=1.0, min_fee=20, slippage_ticks=1))
    a = overnight.build_trades(daily, p).set_index("code").loc["1101"]
    assert (a.buy_px, a.sell_px) == (110.0, 114.5)  # 只有賣出滑 1 檔


def test_fill_adjusted_and_breakeven():
    t = pd.DataFrame({"gap_up": [True, True, False], "net_ret": [0.03, 0.01, -0.02]})
    full = overnight.fill_adjusted(t, 1.0)
    assert full["每張委託期望淨報酬"] == pytest.approx(0.02 / 3)
    half = overnight.fill_adjusted(t, 0.5)
    assert half["預期成交數"] == pytest.approx(2.0)
    assert half["每筆成交平均淨報酬"] == pytest.approx(0.0)
    assert overnight.breakeven_fill(t) == pytest.approx(0.5)
    assert overnight.breakeven_fill(t[t["gap_up"]]) == 0.0
    assert overnight.breakeven_fill(t[~t["gap_up"]]) == float("inf")


def test_orb_long_excludes_prev_limit_up():
    rows = []
    for code, last_close in (("1101", 110.0), ("2202", 108.0)):  # 1101 收漲停、2202 漲 8% 未漲停
        for i in range(20):
            rows.append(bar(i, code, 100, 101, 99, 100, value=1e9))
        rows.append(bar(20, code, 101, 110, 101, last_close, value=1e9))
        rows.append(bar(21, code, 108, 109, 103, 104, lu=121.0, ld=99.0, value=1e9))
    daily = pd.DataFrame(rows)
    base = orb.Params(side=orb.LONG, liquidity_days=5)
    both = orb.select_candidates(daily, base)
    assert set(both[both["trade_date"] == DAYS[21]]["code"]) == {"1101", "2202"}
    assert orb.LONG_B.exclude_prev_limit_up
    only = orb.select_candidates(daily, orb.Params(side=orb.LONG, liquidity_days=5, exclude_prev_limit_up=True))
    assert set(only[only["trade_date"] == DAYS[21]]["code"]) == {"2202"}


def test_block_opposite():
    ts = pd.Timestamp

    def tr(code, side, a, b):
        return dict(code=code, side=side, entry_ts=ts(a), last_exit_ts=ts(b), net_ret=0.01)

    books = {
        "long_a": pd.DataFrame([tr("1101", "long", "2021-03-02 13:30", "2021-03-03 09:00")]),
        "short_a": pd.DataFrame([tr("1101", "short", "2021-03-03 09:00", "2021-03-03 13:20"),
                                 tr("2202", "short", "2021-03-03 09:00", "2021-03-03 10:00")]),
        "long_b": pd.DataFrame([tr("1101", "long", "2021-03-03 09:20", "2021-03-03 11:00"),   # 做空 A 還在 → 擋
                                tr("2202", "long", "2021-03-03 10:30", "2021-03-03 11:00"),   # 做空 A 已回補 → 可
                                tr("3303", "long", "2021-03-03 09:20", "2021-03-03 11:00")]),
    }
    kept, blocked = block_opposite(books)
    assert len(kept["long_a"]) == 1 and len(kept["short_a"]) == 2  # 開盤出清後同一時間放空不算重疊
    assert set(kept["long_b"]["code"]) == {"2202", "3303"}
    assert blocked[["strategy", "code", "blocked_by"]].values.tolist() == [["long_b", "1101", "short_a"]]
