# ST：台股當沖／隔日沖策略回測

依《台股當沖與隔日沖策略重新評估：2020–2026》分階段實作。語言：Python 3.12。

## 安裝

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

## 日 K 資料（階段 1）

資料來源全部免費、官方：

| 資料源 | 內容 |
|---|---|
| `twse_quotes` | 證交所 MI_INDEX 上市每日收盤行情 |
| `twse_limits` | 證交所 TWT84U 當日官方漲停價、跌停價、參考價 |
| `tpex_quotes` | 櫃買中心上櫃行情，含次日參考價與漲跌停價 |

```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python -m st.data fetch --start 2017-04-01   # 可中斷，重跑自動續抓
.venv\Scripts\python -m st.data build                      # → data/daily.parquet
.venv\Scripts\python -m st.data check                      # 資料品質檢查
```

## 策略

| 模組 | 策略 | 需要資料 |
|---|---|---|
| `strategies/short_a.py` `build_trades` | 做空 A 基準（漲停次日開盤空、收盤補） | 日 K |
| `strategies/short_a.py` `simulate_intraday` | 做空 A 加停損、延後進場、13:20 回補 | T+1 分 K |
| `strategies/long_a.py` | 做多 A 尾盤鎖漲停隔日沖（三種成交假設） | T、T+1 分 K |
| `strategies/orb.py` `LONG_B`／`SHORT_B` | 做多 B／做空 B 開盤區間突破 | 當日與前一日分 K |

分 K 放在 `data/minute/<code>/<yyyymm>.parquet`（見 `st.data.minute.BarStore`），
由 `st.backtest.runner` 逐筆載入模擬；缺分 K 的事件會計數並略過。

注意事項：
- 價格一律是原始價格（未還原），`ref`／`limit_up`／`limit_down` 是官方值。判斷收盤漲停請用 `close == limit_up`。
- 無漲跌幅限制的標的（境外 ETF 等）漲跌停價為 NaN。
- `st.core.prices.limit_up()` 的自算值在除權息日等少數情況會和官方值不同，回測事件定義一律用官方值。
