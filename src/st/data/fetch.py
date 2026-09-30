"""證交所／櫃買中心每日資料下載器。

- 每個資料源每天一個 JSON，原樣存到 data/raw/<source>/<yyyy>/<yyyymmdd>.json
- 檔案已存在就跳過（斷點續抓）；休市日也存檔，避免重抓
- 同一主機的請求至少間隔 min_interval 秒，不同主機可以並行
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
RAW_DIR = DATA_DIR / "raw"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) st-research/0.1"}


@dataclass(frozen=True)
class Source:
    name: str
    host: str
    url: Callable[[date], str]
    has_data: Callable[[dict], bool]


def _twse_ok(d: dict) -> bool:
    return d.get("stat") == "OK"


def _tpex_ok(d: dict) -> bool:
    tables = d.get("tables") or []
    return bool(tables) and bool(tables[0].get("data"))


SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        # 上市每日收盤行情（不含權證、牛熊證）
        Source(
            "twse_quotes",
            "www.twse.com.tw",
            lambda d: "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
            f"?date={d:%Y%m%d}&type=ALLBUT0999&response=json",
            _twse_ok,
        ),
        # 上市股價升降幅度：當日官方漲停價、跌停價、開盤競價基準（參考價）
        Source(
            "twse_limits",
            "www.twse.com.tw",
            lambda d: "https://www.twse.com.tw/rwd/zh/variation/TWT84U"
            f"?date={d:%Y%m%d}&selectType=ALL&response=json",
            _twse_ok,
        ),
        # 上櫃每日收盤行情，含「次日」參考價、漲停價、跌停價
        Source(
            "tpex_quotes",
            "www.tpex.org.tw",
            lambda d: "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
            f"?date={d:%Y/%m/%d}&response=json",
            _tpex_ok,
        ),
    ]
}


def raw_path(source: str, d: date) -> Path:
    return RAW_DIR / source / f"{d:%Y}" / f"{d:%Y%m%d}.json"


def weekdays(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def default_end() -> date:
    """15:00 以前當天資料還沒出來，只抓到昨天。"""
    now = datetime.now()
    return now.date() if now.hour >= 15 else now.date() - timedelta(days=1)


class HostThrottle:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.min_interval + random.uniform(0, 1) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def _get_json(session: requests.Session, url: str, throttle: HostThrottle, retries: int = 5) -> dict:
    for attempt in range(retries):
        throttle.wait()
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            # 證交所部分舊資料含未跳脫的控制字元，要 strict=False
            return json.loads(r.text, strict=False)
        except (requests.RequestException, ValueError) as e:
            backoff = 30 * (attempt + 1)
            log.warning("GET 失敗（%s），%ds 後重試：%s", e, backoff, url)
            time.sleep(backoff)
    raise RuntimeError(f"重試 {retries} 次仍失敗：{url}")


def fetch_source(source: Source, start: date, end: date, min_interval: float) -> None:
    session = requests.Session()
    throttle = HostThrottle(min_interval)
    todo = [d for d in weekdays(start, end) if not raw_path(source.name, d).exists()]
    log.info("%s：待下載 %d 天", source.name, len(todo))
    for i, d in enumerate(todo, 1):
        data = _get_json(session, source.url(d), throttle)
        path = raw_path(source.name, d)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        if i % 20 == 0 or i == len(todo):
            log.info("%s：%d/%d（%s，%s）", source.name, i, len(todo), d,
                     "有資料" if source.has_data(data) else "休市")


def fetch(sources: list[str], start: date, end: date, min_interval: float = 4.0) -> None:
    """同一主機的資料源依序抓，不同主機並行。"""
    by_host: dict[str, list[Source]] = {}
    for name in sources:
        s = SOURCES[name]
        by_host.setdefault(s.host, []).append(s)

    def run(host_sources: list[Source]) -> None:
        for s in host_sources:
            fetch_source(s, start, end, min_interval)

    threads = [threading.Thread(target=run, args=(ss,), name=h) for h, ss in by_host.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
