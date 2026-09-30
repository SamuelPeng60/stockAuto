import pytest

from st.core.costs import CostModel


def test_fee_and_tax():
    c = CostModel(discount=0.2, min_fee=1)
    assert c.fee(100_000) == 28  # 100000 × 0.1425% × 0.2 = 28.5 → 捨去
    assert c.fee(100) == 1  # 最低 1 元
    assert CostModel(discount=1.0, min_fee=20).fee(1000) == 20
    assert c.tax(100_000, daytrade=True) == 150
    assert c.tax(100_000, daytrade=False) == 300


def test_round_trip():
    c = CostModel(discount=0.2)
    assert c.round_trip(100_000, 101_000, daytrade=True) == 28 + 28 + 151


@pytest.mark.parametrize(
    "discount, daytrade, pct",
    # 報告中的基準：2 折當沖約 0.207%、隔日沖約 0.357%；無折扣 0.435%、0.585%
    [(0.2, True, 0.00207), (0.2, False, 0.00357), (1.0, True, 0.00435), (1.0, False, 0.00585)],
)
def test_round_trip_pct_matches_report(discount, daytrade, pct):
    assert CostModel(discount=discount).round_trip_pct(daytrade) == pytest.approx(pct)
