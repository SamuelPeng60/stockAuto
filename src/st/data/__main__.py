"""用法：
  python -m st.data fetch --start 2017-04-01 [--end 2026-09-29] [--sources twse_quotes,twse_limits,tpex_quotes]
  python -m st.data kbars --start 2024-01-01 --end 2026-10-02 [--dry-run] [--max-requests 20]
  python -m st.data build
  python -m st.data check
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from .build import build, load_daily
from .check import summary
from .fetch import SOURCES, default_end, fetch


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m st.data")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="下載原始 JSON（可中斷、重跑會續抓）")
    f.add_argument("--start", type=date.fromisoformat, required=True)
    f.add_argument("--end", type=date.fromisoformat, default=None)
    f.add_argument("--sources", default=",".join(SOURCES))
    f.add_argument("--interval", type=float, default=4.0, help="同一主機請求最小間隔秒數")
    k = sub.add_parser("kbars", help="Shioaji 1 分 K：只抓做多 A／做多 B 用得到的股票日（需 API Key）")
    k.add_argument("--start", required=True)
    k.add_argument("--end", required=True)
    k.add_argument("--markets", default="TWSE")
    k.add_argument("--dry-run", action="store_true", help="只列出需求量，不登入、不下載")
    k.add_argument("--max-requests", type=int, default=None, help="本次最多幾個請求（試抓用）")
    k.add_argument("--interval", type=float, default=0.5, help="請求間隔秒數")
    sub.add_parser("build", help="原始 JSON → data/daily.parquet")
    sub.add_parser("check", help="資料品質檢查")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s")
    if a.cmd == "fetch":
        fetch(a.sources.split(","), a.start, a.end or default_end(), a.interval)
    elif a.cmd == "kbars":
        from . import kbars

        daily = load_daily()
        need = kbars.plan(daily, a.start, a.end, kbars.Scope(markets=tuple(a.markets.split(","))))
        done = kbars.load_done()
        left = need[[(c, d) not in done for c, d in zip(need["code"], need["date"])]]
        cal = daily.loc[daily["market"].isin(a.markets.split(",")), "date"]
        print(f"日 K 到 {daily['date'].max().date()}｜需要 {len(need):,} 個股票日（{need['code'].nunique()} 檔），"
              f"已抓 {len(need) - len(left):,}，待抓 {len(left):,}＝{len(kbars.to_ranges(left, cal)) if len(left) else 0:,} 個請求")
        if not a.dry_run:
            kbars.fetch_kbars(need, cal, interval=a.interval, max_requests=a.max_requests)
    elif a.cmd == "build":
        build()
    elif a.cmd == "check":
        print(summary(load_daily()))


if __name__ == "__main__":
    main()
