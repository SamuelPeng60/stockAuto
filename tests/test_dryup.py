import pandas as pd
import pytest

from st.core.costs import CostModel
from st.strategies import dryup

DAYS = pd.bdate_range("2021-03-01", periods=80)
P = dryup.Params(cost=CostModel(discount=1.0, min_fee=20, slippage_ticks=0), lookback=20, ma_days=20,
                 min_value=1e6, cooldown=5, red_window=5)


def series(code, vols, closes, *, opens=None, refs=None):
    """每天開高低收都等於 close（除非另給 opens）；ref 預設是前一天收盤。"""
    rows = []
    for i, (v, c) in enumerate(zip(vols, closes)):
        o = c if opens is None else opens.get(i, c)
        ref = (closes[i - 1] if i else c) if refs is None or i not in refs else refs[i]
        rows.append(dict(date=DAYS[i], market="TWSE", code=code, name=code, kind="stock", open=o, high=max(o, c),
                         low=min(o, c), close=c, change=0.0, volume=v, value=v * c, trades=1, ref=ref,
                         limit_up=round(ref * 1.1, 2), limit_down=round(ref * 0.9, 2)))
    return rows


@pytest.fixture
def daily():
    # A：平常量 30 萬（第 10 天爆量 100 萬），第 25、26 天縮到 5 萬（最大量的 5%），之後回到 30 萬
    #    第 27 天開 100 收 105（長紅），第 29 天除息：參考價 100、收 100（前一天收 105）
    vols = [300_000] * 40
    vols[10], vols[25], vols[26] = 1_000_000, 50_000, 50_000
    closes = [100.0] * 27 + [105.0, 105.0] + [100.0] * 11
    rows = series("1101", vols, closes, opens={27: 100.0}, refs={29: 100.0})
    # B：一直沒量也沒縮（對照組，讓 bench 有值）
    rows += series("2202", [100_000] * 40, [50.0] * 40)
    return pd.DataFrame(rows)


def test_signal_and_first_of_cluster(daily):
    d = dryup.prepare(daily, P)
    t = dryup.build_trades(d, P, hold=3)
    a = t[t["code"] == "1101"]
    # 第 25、26 天都符合，只取第一天；T+1 = 第 26 天開盤買
    assert list(a["event_date"]) == [DAYS[25]]
    assert a.iloc[0]["trade_date"] == DAYS[26] and a.iloc[0]["exit_date"] == DAYS[28]
    assert a.iloc[0]["vol_ratio"] == pytest.approx(0.05)
    assert a.iloc[0]["days_to_red"] == 2 and a.iloc[0]["red_hit"]
    assert not len(t[t["code"] == "2202"])
    assert len(dryup.scan(d, P, DAYS[26])) == 1  # scan 不套 cooldown


def test_returns_use_ref_price(daily):
    d = dryup.prepare(daily, P)
    # 持有 3 日：第 26 天開 100 買 → 第 28 天收 105 賣
    a = dryup.build_trades(d, P, hold=3).iloc[0]
    assert a["gross_ret"] == pytest.approx(0.05)
    assert a["net_ret"] == pytest.approx(0.05 - 0.00585)
    assert a["bench_ret"] == pytest.approx(0.025)  # 兩檔平均：5% 與 0%
    # 持有 4 日：第 29 天除息，收 100 但參考價也是 100 → 當天報酬 0，累計仍是 +5%
    b = dryup.build_trades(d, P, hold=4).iloc[0]
    assert b["gross_ret"] == pytest.approx(0.05)


def test_flags_can_be_relaxed(daily):
    d = dryup.prepare(daily, P)
    strict = dryup.build_trades(d, dryup.Params(**{**P.__dict__, "dry_ratio": 0.04}), hold=3)
    assert not len(strict)
    loose = dryup.build_trades(d, P, hold=3, flags=("f_liquid",))
    assert set(loose["code"]) == {"1101", "2202"}
