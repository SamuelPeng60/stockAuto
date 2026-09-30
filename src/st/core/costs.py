"""交易成本模型（2026 年）。

- 手續費：0.1425% × 折扣，買賣各收一次，未滿 1 元捨去，有每筆最低金額
- 證交稅（賣出時收）：現股當沖 0.15%（優惠至 2027/12/31），一般 0.3%
- 滑價：每邊不利方向移動 N 檔，在 prices.shift_ticks 處理
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FEE_RATE = 0.001425
TAX_DAYTRADE = 0.0015
TAX_NORMAL = 0.003


@dataclass(frozen=True)
class CostModel:
    discount: float = 0.2  # 永豐大戶投線上新戶 2 折；無折扣填 1.0
    min_fee: int = 1  # 大戶投每筆最低 1 元；一般券商 20 元
    slippage_ticks: int = 1

    def fee(self, amount: float) -> int:
        return max(self.min_fee, math.floor(amount * FEE_RATE * self.discount))

    @staticmethod
    def tax(sell_amount: float, daytrade: bool) -> int:
        return math.floor(sell_amount * (TAX_DAYTRADE if daytrade else TAX_NORMAL))

    def round_trip(self, buy_amount: float, sell_amount: float, daytrade: bool) -> int:
        """一買一賣的總成本（元），不含滑價。"""
        return self.fee(buy_amount) + self.fee(sell_amount) + self.tax(sell_amount, daytrade)

    def round_trip_pct(self, daytrade: bool) -> float:
        """來回成本占部位比例的近似值（忽略最低手續費與捨去），用於日頻向量化回測。"""
        return 2 * FEE_RATE * self.discount + (TAX_DAYTRADE if daytrade else TAX_NORMAL)
