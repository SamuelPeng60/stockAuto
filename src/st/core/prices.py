"""台股升降單位（檔位）與漲跌停價計算。

全部用 Decimal 計算，避免浮點誤差讓「收盤價 == 漲停價」判斷失準。

規則（證交所／櫃買中心）：
- 股票檔位：<10 元 0.01；10–50 0.05；50–100 0.1；100–500 0.5；500–1000 1；>=1000 5
- ETF 檔位：<50 元 0.01；>=50 0.05
- 漲停價 = 參考價 × 1.1，不足一檔的部分無條件捨去；跌停價 = 參考價 × 0.9，無條件進位。
  檔位依「計算後的價格」所在區間決定。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

Number = Decimal | float | int | str

_STOCK_TICKS = [
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.1")),
    (Decimal("500"), Decimal("0.5")),
    (Decimal("1000"), Decimal("1")),
]
_STOCK_TOP_TICK = Decimal("5")

_ETF_TICKS = [(Decimal("50"), Decimal("0.01"))]
_ETF_TOP_TICK = Decimal("0.05")


def _d(x: Number) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def is_etf(code: str) -> bool:
    """ETF／受益憑證代號以 00 開頭（例：0050、00878、006208）。"""
    return code.startswith("00")


def tick_size(price: Number, etf: bool = False) -> Decimal:
    p = _d(price)
    table, top = (_ETF_TICKS, _ETF_TOP_TICK) if etf else (_STOCK_TICKS, _STOCK_TOP_TICK)
    for upper, tick in table:
        if p < upper:
            return tick
    return top


def _snap(price: Decimal, etf: bool, rounding: str) -> Decimal:
    tick = tick_size(price, etf)
    return (price / tick).to_integral_value(rounding=rounding) * tick


def ceil_to_tick(price: Number, etf: bool = False) -> Decimal:
    """無條件進位到合法檔位（停損價等「至少要到這個價位」的情況）。"""
    return _snap(_d(price), etf, ROUND_CEILING)


def limit_up(ref: Number, etf: bool = False, pct: Number = "0.10") -> Decimal:
    return _snap(_d(ref) * (1 + _d(pct)), etf, ROUND_FLOOR)


def limit_down(ref: Number, etf: bool = False, pct: Number = "0.10") -> Decimal:
    return _snap(_d(ref) * (1 - _d(pct)), etf, ROUND_CEILING)


def shift_ticks(price: Number, n: int, etf: bool = False) -> Decimal:
    """把價格往上（n>0）或往下（n<0）移動 n 檔，處理跨越檔位區間的情況。用於滑價模擬。"""
    p = _d(price)
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        if step > 0:
            p += tick_size(p, etf)
        else:
            # 往下時用「下方」價格的檔位，例如 50.00 往下一檔是 49.95
            p -= tick_size(p - Decimal("0.000001"), etf)
    return p
