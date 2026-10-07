# Shioaji 金鑰申請步驟

用途：下載上市 2024–2025 的 1 分 K（整理區間突破策略用）。

整理自 Shioaji 官方文件（2026-10-07 查）。網站畫面上的實際字樣可能和文件略有出入。

## 一、先確認有永豐金證券戶

Shioaji 是永豐金證券的 API，一定要有永豐金的證券戶才能用。

- **已經有戶頭**：直接跳到第二步。
- **還沒有**：到[永豐金開戶頁面](https://www.sinotrade.com.tw/openact?strProd=0254&strWeb=0683&s=013299&utm_source=shioaji)，選「我要開 DAWHO+大戶投」。這會同時開永豐銀行戶（當交割戶）和證券戶，開戶需要審核時間。

## 二、申請 API Key

1. 登入永豐金證券理財網，進入 [API 管理頁面](https://www.sinotrade.com.tw/newweb/PythonAPIKey/)（在「個人服務」裡）。
2. 點「新增 API KEY」。
3. 用手機或信箱做雙因子驗證。
4. 填設定：

   | 項目 | 怎麼填 |
   |---|---|
   | 到期時間 | 自己訂。資料可能要分好幾天抓，建議一個月以上 |
   | 權限：行情／資料 | **勾** |
   | 權限：正式環境 | **勾**（模擬環境不保證有完整的歷史分 K） |
   | 權限：帳務 | 不勾 |
   | 權限：交易 | 不勾（金鑰就算外流也不能下單） |
   | IP 限制 | 可填家裡的對外 IP 提高安全性；IP 會變動就先留空 |
   | 帳戶 | 勾你的證券戶 |

5. 送出後畫面會顯示 **API Key** 和 **Secret Key**。

> **Secret Key 只會顯示這一次**，之後查不到。請當下複製存好；弄丟只能刪掉重新申請。

只抓行情用不到憑證檔（下單才要），可以不下載。

## 三、服務條款簽署（可能需要）

官方規定新用戶要：

1. 到[簽署中心](https://www.sinotrade.com.tw/newweb/signCenter/signCenterIndex/)簽署證券 API 條款。
2. 在模擬模式完成登入與下單測試。

兩項都完成才能在正式環境下單。文件沒有明說「只查行情」要不要先做這兩項。

建議：先把**證券簽署**做掉（線上勾選）。下單測試先不用做；如果試抓時登入被擋，再補做。

測試報告服務時間是週一到週五 08:00–20:00，18:00–20:00 限台灣 IP。

## 四、把金鑰放進專案

在 `C:\Users\rd7\Desktop\WORK\ST\` 建一個名為 `.env` 的純文字檔，內容兩行：

```
SHIOAJI_API_KEY=貼上你的 API Key
SHIOAJI_SECRET_KEY=貼上你的 Secret Key
```

注意：

- 等號兩邊不要空格，不要加引號。
- 用記事本存檔時，「存檔類型」選「所有檔案」，檔名打 `.env`，不然會變成 `.env.txt`。
- 這個檔已列在 `.gitignore`，不會進版控。
- **不要把金鑰貼到對話裡**，放進檔案就好，程式會自己讀。

## 五、完成後

回到 Claude Code 說「好了」，並確認同意執行 `pip install shioaji`。接下來的流程：

1. 安裝 `shioaji`。
2. 試抓 20 個請求，驗證資料格式（時間戳是 K 棒開始還是結束、成交量單位是股還是張）與實際流量。
3. 回報全部抓完要多久，再決定是否全抓。

## 下載量與限制

| 項目 | 數字 |
|---|---|
| 範圍 | 上市，2024/1/1–2025/12/31 |
| 需求 | 12,386 個股票日、634 檔、7,947 個請求 |
| 歷史分 K 起始日 | 2020/3/2 |
| 盤中查詢上限 | kbars 270 次，所以要在**收盤後**抓 |
| 查詢頻率 | 10 秒內最多 50 次，超過暫停一分鐘 |
| 每日流量上限 | 當日無成交 500 MB；有成交 2 GB；成交超過 1 億 10 GB |
| 超過流量 | 查詢回傳空值 |
| 單次查詢區間 | 不得超過 30 天 |

實際一個請求多大要試抓才知道。帳戶當天沒有成交的話上限是 500 MB，可能要分幾天抓完；下載器會記錄進度，隔天重跑會接著抓。

## 參考

- [Shioaji 開戶](https://sinotrade.github.io/zh/tutor/prepare/open_account/)
- [Shioaji 金鑰與憑證申請](https://sinotrade.github.io/zh/tutor/prepare/token/)
- [Shioaji 服務條款簽署與測試](https://sinotrade.github.io/zh/tutor/prepare/terms/)
- [Shioaji 使用限制](https://sinotrade.github.io/zh/tutor/limit/)
- [Shioaji 歷史行情](https://sinotrade.github.io/zh/tutor/market_data/historical/)
