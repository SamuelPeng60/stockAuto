import pandas as pd

from st.core.calendar import next_day_map, prev_day_map

T = pd.Timestamp


def test_holiday_kept_gap_broken():
    # 2019 春節：1/30 收盤後休市，2/11 開盤（相隔 12 天，屬正常休市）
    dates = pd.to_datetime(["2019-01-29", "2019-01-30", "2019-02-11", "2025-09-01", "2025-09-02"])
    nxt, prv = next_day_map(dates), prev_day_map(dates)
    assert nxt[T("2019-01-29")] == T("2019-01-30")
    assert nxt[T("2019-01-30")] == T("2019-02-11")
    assert pd.isna(nxt[T("2019-02-11")])  # 資料缺口，不能對應到 2025
    assert pd.isna(prv[T("2025-09-01")])
    assert prv[T("2025-09-02")] == T("2025-09-01")
    assert pd.isna(nxt[T("2025-09-02")])
