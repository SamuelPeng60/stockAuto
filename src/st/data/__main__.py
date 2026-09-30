"""用法：
  python -m st.data fetch --start 2017-04-01 [--end 2026-09-29] [--sources twse_quotes,twse_limits,tpex_quotes]
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
    sub.add_parser("build", help="原始 JSON → data/daily.parquet")
    sub.add_parser("check", help="資料品質檢查")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s")
    if a.cmd == "fetch":
        fetch(a.sources.split(","), a.start, a.end or default_end(), a.interval)
    elif a.cmd == "build":
        build()
    elif a.cmd == "check":
        print(summary(load_daily()))


if __name__ == "__main__":
    main()
