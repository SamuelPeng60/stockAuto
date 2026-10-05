"""Shioaji 1 分 K 下載器：只抓策略用得到的（股票, 日期），存進 BarStore。

- 需求清單由日 K 算出（plan）：做多 A 要 T 與 T+1，做多 B 要交易日與前一交易日（算 ATR）
- 每檔股票把相鄰的交易日併成一段，一段一個請求；抓過的（含沒資料的）記在
  data/minute/_done.csv，重跑會跳過（斷點續抓）
- 流量：每 usage_every 個請求查一次 api.usage()，剩餘額度低於 min_remaining_mb 就停
- 金鑰讀環境變數 SHIOAJI_API_KEY／SHIOAJI_SECRET_KEY，或專案根目錄的 .env（已列入 .gitignore）

Shioaji kbars 的格式假設（第一次實際下載時必須驗證，見 check_bars）：
- ts 是 K 棒「結束」時間（09:01 那根含開盤集合競價，13:30 那根是收盤集合競價）→ 轉成本專案的「開始」時間
- Volume 單位是張 → ×1000 轉成股，才能和 Amount（元）算 VWAP
"""

from __future__ import annotations

import csv
import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pandas as pd

from st.core.calendar import prev_day_map
from st.strategies import long_a, orb

from .minute import MINUTE_DIR, BarStore

log = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
DONE_PATH = MINUTE_DIR / "_done.csv"
LAST_CONTINUOUS = time(13, 25)  # 結束時間晚於此的 K 棒 = 收盤集合競價


# ---------- 需求清單 ----------

@dataclass(frozen=True)
class Scope:
    """要抓哪些策略的分 K。預設：做多 A 只留成交值門檻（濾網可在回測時再加），做多 B 用預設選股。"""
    markets: tuple[str, ...] = ("TWSE",)
    long_a: long_a.Params | None = long_a.Params(use_consolidation=False, use_volume_high=False)
    long_b: orb.Params | None = orb.LONG_B
    long_b_prev_day: bool = True  # 前一交易日的分 K（ATR 用）


def plan(daily: pd.DataFrame, start: str, end: str, scope: Scope = Scope()) -> pd.DataFrame:
    """回傳需要的 (code, date)，date 落在 [start, end]。"""
    d = daily[daily["market"].isin(scope.markets)]
    need: set[tuple[str, pd.Timestamp]] = set()
    if scope.long_a is not None:
        c = long_a.select_candidates(d, scope.long_a)
        need |= set(zip(c["code"], c["event_date"])) | set(zip(c["code"], c["next_date"]))
    if scope.long_b is not None:
        c = orb.select_candidates(d, scope.long_b)
        need |= set(zip(c["code"], c["trade_date"]))
        if scope.long_b_prev_day:
            need |= set(zip(c["code"], c["trade_date"].map(prev_day_map(d["date"]))))
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    rows = [(code, day) for code, day in need if pd.notna(day) and lo <= day <= hi]
    return pd.DataFrame(rows, columns=["code", "date"]).sort_values(["code", "date"]).reset_index(drop=True)


def to_ranges(need: pd.DataFrame, calendar) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """每檔股票把「相鄰交易日」併成 (code, 起, 迄)，一段一個請求，不會多抓中間用不到的日子。"""
    cal = pd.DatetimeIndex(sorted(pd.unique(pd.Series(calendar))))
    pos = {day: i for i, day in enumerate(cal)}
    out = []
    for code, g in need.groupby("code", sort=True):
        days = sorted(g["date"])
        a = b = days[0]
        for day in days[1:]:
            if pos.get(day, -9) == pos.get(b, -99) + 1:
                b = day
            else:
                out.append((code, a, b))
                a = b = day
        out.append((code, a, b))
    return out


def load_done(path: Path = DONE_PATH) -> set[tuple[str, pd.Timestamp]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {(r[0], pd.Timestamp(r[1])) for r in csv.reader(f) if r}


def _mark_done(rows: list[tuple[str, pd.Timestamp, int]], path: Path = DONE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows((c, f"{d:%Y-%m-%d}", n) for c, d, n in rows)


# ---------- 格式轉換 ----------

def to_bars(kbars) -> pd.DataFrame:
    """Shioaji kbars（dict-like：ts, Open, High, Low, Close, Volume, Amount）→ BarStore 格式。"""
    k = pd.DataFrame({**kbars})
    if k.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "amount"])
    end = pd.to_datetime(k["ts"], unit="ns")  # Shioaji 給奈秒整數（台灣時間，不帶時區）
    start = end - pd.Timedelta(minutes=1)
    auction = end.dt.time > LAST_CONTINUOUS
    return pd.DataFrame({
        "ts": start.where(~auction, end.dt.normalize() + pd.Timedelta(hours=13, minutes=30)),
        "open": k["Open"].astype(float), "high": k["High"].astype(float),
        "low": k["Low"].astype(float), "close": k["Close"].astype(float),
        "volume": k["Volume"].astype(float) * 1000, "amount": k["Amount"].astype(float),
    }).sort_values("ts").reset_index(drop=True)


def check_bars(bars: pd.DataFrame) -> list[str]:
    """格式假設的健全性檢查，回傳問題清單（空 = 沒問題）。"""
    if bars.empty:
        return []
    out = []
    t = bars["ts"].dt.time
    if t.min() < time(9, 0) or t.max() > time(13, 30):
        out.append(f"時間超出 09:00–13:30：{t.min()} ~ {t.max()}")
    if bars["ts"].duplicated().any():
        out.append("ts 重複")
    v = bars[bars["volume"] > 0]
    avg = v["amount"] / v["volume"]  # 單位正確時應該落在該根的最低～最高之間
    off = ((avg < v["low"] * 0.98) | (avg > v["high"] * 1.02)).mean() if len(v) else 0.0
    if off > 0.05:
        out.append(f"{off:.0%} 的 K 棒 成交金額／成交量 不在高低價之間（Volume 單位可能不是張）")
    return out


# ---------- 下載 ----------

def read_credentials() -> tuple[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip("'\""))
    try:
        return env["SHIOAJI_API_KEY"], env["SHIOAJI_SECRET_KEY"]
    except KeyError as e:
        raise RuntimeError(f"找不到 {e.args[0]}：請設環境變數，或寫在 {ENV_PATH}") from None


def fetch_kbars(need: pd.DataFrame, calendar, store: BarStore | None = None, *, interval: float = 0.5,
                usage_every: int = 50, min_remaining_mb: float = 50, max_requests: int | None = None) -> None:
    import shioaji as sj  # 延後匯入：沒裝 shioaji 也能跑 plan 與測試

    store = store or BarStore()
    done = load_done()
    todo = need[[(c, d) not in done for c, d in zip(need["code"], need["date"])]]
    ranges = to_ranges(todo, calendar) if len(todo) else []
    if max_requests is not None:
        ranges = ranges[:max_requests]
    log.info("需要 %d 個股票日，已有 %d，本次 %d 個請求", len(need), len(need) - len(todo), len(ranges))
    if not ranges:
        return

    api_key, secret = read_credentials()
    api = sj.Shioaji()
    api.login(api_key=api_key, secret_key=secret)
    wanted = {code: list(g["date"]) for code, g in todo.groupby("code")}
    try:
        for i, (code, a, b) in enumerate(ranges, 1):
            if (i - 1) % usage_every == 0:
                u = api.usage()
                log.info("流量：已用 %.1f MB／上限 %.0f MB", u.bytes / 1e6, u.limit_bytes / 1e6)
                if u.remaining_bytes / 1e6 < min_remaining_mb:
                    log.warning("剩餘流量不足 %.0f MB，停止；明天重跑會續抓", min_remaining_mb)
                    return
            days = [d for d in wanted[code] if a <= d <= b]
            contract = api.Contracts.Stocks[code]
            if contract is None:  # 已下市等查不到商品檔
                log.warning("%s 查不到商品檔，略過", code)
                _mark_done([(code, d, -1) for d in days])
                continue
            bars = to_bars(api.kbars(contract=contract, start=f"{a:%Y-%m-%d}", end=f"{b:%Y-%m-%d}"))
            for msg in check_bars(bars):
                log.warning("%s %s~%s：%s", code, a.date(), b.date(), msg)
            if len(bars):
                store.put(code, bars)
            per_day = bars.groupby(bars["ts"].dt.normalize()).size() if len(bars) else {}
            _mark_done([(code, d, int(per_day.get(d, 0))) for d in days])
            if i % 100 == 0 or i == len(ranges):
                log.info("%d/%d（%s %s）", i, len(ranges), code, b.date())
            _time.sleep(interval)
    finally:
        api.logout()
