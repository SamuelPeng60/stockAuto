"""把日 K 選出的候選事件，逐筆載入分 K 模擬，彙整成交易表。缺分 K 的事件會計數並略過。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from st.backtest.intraday import Limits
from st.core.calendar import prev_day_map
from st.core.costs import CostModel
from st.core.prices import is_etf
from st.data.minute import BarStore
from st.strategies import long_a, orb, short_a


@dataclass
class RunResult:
    trades: pd.DataFrame
    candidates: int
    missing_bars: int
    no_signal: int

    def describe(self) -> str:
        return (f"候選 {self.candidates:,}｜缺分 K {self.missing_bars:,}｜"
                f"條件未觸發 {self.no_signal:,}｜成交 {len(self.trades):,}")


def _collect(rows: list[dict], cands: int, missing: int, none: int) -> RunResult:
    return RunResult(pd.DataFrame(rows), cands, missing, none)


def run_short_a(daily: pd.DataFrame, store: BarStore, p: short_a.IntradayParams,
                cost: CostModel, daily_params: short_a.Params = short_a.Params()) -> RunResult:
    ev = short_a.build_trades(daily, daily_params)
    lim_cols = daily[["date", "code", "limit_up", "limit_down"]]
    ev = ev.merge(lim_cols.rename(columns={"date": "trade_date"}), on=["trade_date", "code"], how="left")
    rows, missing, none = [], 0, 0
    for e in ev.itertuples():
        bars = store.get(e.code, e.trade_date)
        if bars is None:
            missing += 1
            continue
        tr = short_a.simulate_intraday(e.code, bars, Limits(e.limit_up, e.limit_down, is_etf(e.code)),
                                       e.close, p)
        if tr is None:
            none += 1
            continue
        rows.append(tr.to_row(cost, daytrade=True) | {"event_date": e.event_date})
    return _collect(rows, len(ev), missing, none)


def run_long_a(daily: pd.DataFrame, store: BarStore, p: long_a.Params, cost: CostModel) -> RunResult:
    cands = long_a.select_candidates(daily, p)
    rows, missing, none = [], 0, 0
    for c in cands.itertuples():
        bt, bt1 = store.get(c.code, c.event_date), store.get(c.code, c.next_date)
        if bt is None or bt1 is None:
            missing += 1
            continue
        etf = is_etf(c.code)
        tr = long_a.simulate(c.code, bt, bt1, Limits(c.limit_up, c.limit_down, etf),
                             Limits(c.n_limit_up, c.n_limit_down, etf), p)
        if tr is None:
            none += 1
            continue
        rows.append(tr.to_row(cost, daytrade=False) | {"next_date": c.next_date})
    return _collect(rows, len(cands), missing, none)


def run_orb(daily: pd.DataFrame, store: BarStore, p: orb.Params, cost: CostModel) -> RunResult:
    cands = orb.select_candidates(daily, p)
    prev_day = prev_day_map(daily["date"])
    rows, missing, none = [], 0, 0
    for c in cands.itertuples():
        bars = store.get(c.code, c.trade_date)
        if bars is None:
            missing += 1
            continue
        prev_date = prev_day.get(c.trade_date)
        prev = store.get(c.code, prev_date) if pd.notna(prev_date) else None
        tr = orb.simulate(c.code, bars, prev, Limits(c.limit_up, c.limit_down, is_etf(c.code)), c.ref, p,
                          short_below_ref_ok=not c.prev_close_limit_down)
        if tr is None:
            none += 1
            continue
        rows.append(tr.to_row(cost, daytrade=True))
    return _collect(rows, len(cands), missing, none)
