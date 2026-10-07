import pandas as pd

from st.data import kbars

DAYS = pd.bdate_range("2024-03-01", periods=8)


def test_to_ranges_merges_adjacent_trading_days():
    need = pd.DataFrame({"code": ["1101", "1101", "1101", "1101", "2330"],
                         "date": [DAYS[0], DAYS[1], DAYS[3], DAYS[4], DAYS[2]]})
    cal = [d for i, d in enumerate(DAYS) if i != 2]  # DAYS[2] 當作休市：DAYS[1] 與 DAYS[3] 相鄰
    assert kbars.to_ranges(need, cal) == [("1101", DAYS[0], DAYS[4]), ("2330", DAYS[2], DAYS[2])]
    assert kbars.to_ranges(need, DAYS) == [("1101", DAYS[0], DAYS[1]), ("1101", DAYS[3], DAYS[4]),
                                          ("2330", DAYS[2], DAYS[2])]


def test_to_bars_converts_end_label_and_lots():
    ts = pd.to_datetime(["2024-03-01 09:01", "2024-03-01 09:02", "2024-03-01 13:25", "2024-03-01 13:30"])
    k = {"ts": ts.as_unit("ns").astype("int64").tolist(), "Open": [100, 101, 102, 103], "High": [101, 102, 103, 103],
         "Low": [100, 101, 102, 103], "Close": [101, 102, 103, 103], "Volume": [5, 2, 1, 30],
         "Amount": [502500.0, 203000.0, 102500.0, 3090000.0]}
    b = kbars.to_bars(k)
    assert [f"{t:%H:%M}" for t in b["ts"]] == ["09:00", "09:01", "13:24", "13:30"]  # 13:30 = 收盤集合競價
    assert b["volume"].tolist() == [5000, 2000, 1000, 30000]
    assert kbars.check_bars(b) == []
    assert kbars.to_bars({"ts": [], "Open": [], "High": [], "Low": [], "Close": [], "Volume": [], "Amount": []}).empty


def test_check_bars_flags_wrong_volume_unit():
    b = pd.DataFrame({"ts": pd.to_datetime(["2024-03-01 09:00"]), "open": [100.0], "high": [101.0], "low": [100.0],
                      "close": [101.0], "volume": [5.0], "amount": [502500.0]})  # 量還是張 → 均價差 1000 倍
    assert any("Volume" in m for m in kbars.check_bars(b))


def test_done_roundtrip(tmp_path):
    p = tmp_path / "_done.csv"
    kbars._mark_done([("1101", DAYS[0], 266), ("1101", DAYS[1], 0)], p)
    assert kbars.load_done(p) == {("1101", DAYS[0]), ("1101", DAYS[1])}


def test_scope_for_breakout_only():
    s = kbars.scope_for(["breakout"], ("TPEX",))
    assert s.long_a is None and s.long_b is None and s.breakout.box_range == 0.15 and s.markets == ("TPEX",)
    assert kbars.scope_for(["long_a", "long_b"], ("TWSE",)) == kbars.Scope()
