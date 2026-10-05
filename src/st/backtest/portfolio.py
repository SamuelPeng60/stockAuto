"""跨策略風控：同一檔股票不得同時持有多空部位。

各策略獨立回測後，把交易表合併、依進場時間排序；新部位進場時，若同一檔已有方向相反且尚未出場的部位，
就擋掉新部位（先進場者優先，和實盤下單前檢查庫存的規則一致）。同一時間進場則依 books 的順序決定優先權。

前一筆在 09:00 開盤出清、後一筆在同一個 09:00 開盤進場不算重疊（例如做多 A 開盤賣出 → 做空 A 開盤放空）。
實盤上這等於同一場集合競價賣出兩倍張數，下單時要分清現股賣出與先賣後買。
"""

from __future__ import annotations

import pandas as pd


def block_opposite(books: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """books：{策略名稱: 交易表}，需有 code、side（"long"／"short"）、entry_ts、last_exit_ts。

    回傳（各策略保留的交易, 被擋掉的交易）；被擋掉的交易多 strategy、blocked_by 兩欄。
    """
    frames = [t.assign(strategy=name, _prio=i) for i, (name, t) in enumerate(books.items()) if len(t)]
    if not frames:
        return dict(books), pd.DataFrame()
    allt = pd.concat(frames, ignore_index=True).sort_values(["entry_ts", "_prio"], kind="stable")

    held: dict[str, list[tuple[str, pd.Timestamp, str]]] = {}  # code → [(side, 出場時間, 策略)]
    keep, blocked_by = [], []
    for r in allt.itertuples():
        open_pos = [h for h in held.get(r.code, []) if h[1] > r.entry_ts]
        clash = next((h[2] for h in open_pos if h[0] != r.side), None)
        keep.append(clash is None)
        blocked_by.append(clash)
        if clash is None:
            open_pos.append((r.side, r.last_exit_ts, r.strategy))
        held[r.code] = open_pos
    allt["blocked_by"] = blocked_by
    kept = allt[keep].drop(columns=["_prio", "blocked_by"])
    out = {name: kept[kept["strategy"] == name].drop(columns="strategy").reset_index(drop=True)
           if len(t) else t for name, t in books.items()}
    return out, allt[[not k for k in keep]].drop(columns="_prio").reset_index(drop=True)
