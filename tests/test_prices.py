from decimal import Decimal as D

import pytest

from st.core.prices import is_etf, limit_down, limit_up, shift_ticks, tick_size


@pytest.mark.parametrize(
    "price, tick",
    [("9.99", "0.01"), ("10", "0.05"), ("49.95", "0.05"), ("50", "0.1"), ("99.9", "0.1"),
     ("100", "0.5"), ("499.5", "0.5"), ("500", "1"), ("999", "1"), ("1000", "5"), ("1280", "5")],
)
def test_stock_tick(price, tick):
    assert tick_size(price) == D(tick)


def test_etf_tick():
    assert tick_size("49.99", etf=True) == D("0.01")
    assert tick_size("50", etf=True) == D("0.05")
    assert is_etf("0050") and is_etf("00878") and is_etf("006201") and not is_etf("2330")


# 以下期望值取自證交所 TWT84U 與櫃買中心行情表的官方漲跌停價
@pytest.mark.parametrize(
    "ref, etf, up, down",
    [
        ("1165", False, "1280", "1050"),  # 2330 2025/09/02：1281.5 捨去到 5 元檔
        ("74.55", True, "82.00", "67.10"),  # 0050 2017/05/02
        ("27.74", True, "30.51", "24.97"),  # 0051 2017/05/02
        ("52.00", True, "57.20", "46.80"),  # 0050 2025/09/02
        ("22.71", True, "24.98", "20.44"),  # 006201 2025/09/02（上櫃）
        ("364.50", False, "400.50", "328.50"),  # 6488 2025/09/02（上櫃）
    ],
)
def test_official_limits(ref, etf, up, down):
    assert limit_up(ref, etf) == D(up)
    assert limit_down(ref, etf) == D(down)


def test_limit_crosses_tick_boundary():
    # 計算後價格跨入新檔位區間時，用新區間的檔位
    assert limit_up("46") == D("50.6")  # 50.6 落在 0.1 檔
    assert limit_up("9.3") == D("10.2")  # 10.23 落在 0.05 檔 → 捨去到 10.20
    assert limit_down("10.5") == D("9.45")  # 9.45 落在 0.01 檔
    assert limit_down("112") == D("101")  # 100.8 落在 0.5 檔 → 進位到 101.0


def test_shift_ticks():
    assert shift_ticks("50", -1) == D("49.95")
    assert shift_ticks("49.95", 1) == D("50.00")
    assert shift_ticks("50", 1) == D("50.1")
    assert shift_ticks("100", -2) == D("99.8")
    assert shift_ticks("1000", 1) == D("1005")
