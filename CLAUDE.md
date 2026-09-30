# CLAUDE.md

台股當沖／隔日沖策略回測與（未來）Shioaji 實盤。依據研究報告《台股當沖與隔日沖策略重新評估：2020–2026》（PDF 不進版控）分階段實作。
使用者以繁體中文溝通，回覆與程式註解一律用繁體中文。

## 指令

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
$env:PYTHONIOENCODING="utf-8"   # Windows 終端機印中文必設
.venv\Scripts\python -m st.data fetch --start 2017-04-01 --sources twse_quotes
.venv\Scripts\python -m st.data build      # data/raw → data/daily.parquet
.venv\Scripts\python -m st.data check
.venv\Scripts\python -m st.backtest short_a [--discount 0.2 --min-fee 1 --slip 0]
```

## 工作規則

- **下載前一定要先問使用者**：會占頻寬。使用者曾明確要求停止下載。長時間下載用分離的背景程序（Start-Process），並告知 PID 與停止方式。
- 只做被要求的階段；每階段完成後回報結果與下一步，由使用者決定是否繼續。
- 回測結果要誠實呈現，特別標明樣本不足、成本假設、含未來資訊的診斷欄位。

## 架構

- `src/st/core/`：`prices.py`（檔位、漲跌停價，全部用 Decimal）、`costs.py`、`calendar.py`
- `src/st/data/`：`fetch.py`（證交所／櫃買下載，同主機間隔 ≥4 秒、檔案存在即跳過）、`parse.py`、`build.py`、`minute.py`（分 K 儲存 `data/minute/<code>/<yyyymm>.parquet`）
- `src/st/backtest/`：`metrics.py`、`segments.py`（報告要求的市場分段）、`intraday.py`（1 分 K 模擬工具）、`runner.py`
- `src/st/strategies/`：`short_a.py`（做空 A 日頻＋盤中）、`long_a.py`（做多 A 隔日沖）、`orb.py`（做多 B／做空 B）

## 關鍵決策與陷阱

- **收盤漲停判斷用 `close == limit_up`**，不用漲幅 ≥9.5%；價格一律原始（未還原）。
- 上市漲跌停價：有官方 TWT84U 就用官方（`limit_src="official"`），否則用「收盤 − 漲跌價差」推參考價再自算（`"calc"`）。實測 2025/9 一週 5,197 筆全部一致。TWT84U 一天約 3.7 MB（含權證），所以預設不抓。
- 上櫃的參考價／漲跌停價來自**前一交易日**行情表的「次日」欄位。
- 無漲跌幅限制的標的（境外 ETF 等）官方標 9999.95／0.01，build 時轉 NaN。
- 交易日曆由資料推導；相鄰日期相隔 >15 天視為資料缺口（`core/calendar.py`），避免跨缺口配對 T／T+1。
- `.gitignore` 用 `/data/`（只排除根目錄）；寫成 `data/` 會連 `src/st/data/` 一起排除。
- 分 K 成交假設：K 棒收盤訊號 → 下一根開盤成交；同根觸及停損與停利視為先停損；滑價不超出漲跌停價。尚未模擬瞬間價格穩定措施。
- 做空 A 的兩腿都在集合競價成交，「每邊 1 檔滑價」可能過度保守；成本假設決定結果正負，報表要同時列出成本敏感度。

## 目前進度（2026-09-30）

- 已下載：上市行情 2017/4/3–2019/1/25、2025/9/1–9/5（`data/raw/`，下載中斷可續抓）。
- 做空 A 日頻回測（2017/4–2019/1，2,914 筆）：2 折、無滑價 勝率 50.9%、淨 +0.272%（論文 52%／+0.24%）；加 1 檔滑價即轉負。20.3% 樣本 T+1 收盤漲停（可能買不回，回測偏樂觀）。
- 分 K 策略（做多 A、ORB、做空 A 盤中版）已完成並有單元測試，但**尚無分 K 資料**。
- 待辦：補齊上市行情 2019/2 以後（約 0.4 GB，需使用者同意）；Shioaji kbars 下載器（需 API Key）；處置股／注意股／當沖標的歷史名單；產業別與當沖比重濾網；walk-forward。
