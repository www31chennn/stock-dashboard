# 台股模擬交易系統 v2

## 系統說明

每天自動從全市場篩選股票、計算技術指標、模擬買賣並計算盈虧。
結果自動部署到 Vercel，手機開網址即可查看。

---

## 架構

```
每天週一到週五 17:00（台灣時間）GitHub Actions 自動執行
  ↓
TWSE API 取得全市場股票代號名單（約 1300+ 支）
  ↓
yfinance 批次抓取並篩選
  條件：收盤價 > 10 元、成交量 > 1,000 張
  → 篩出約 300~500 支
  ↓
計算技術指標（MA、RSI、布林通道、成交量）
  → 找出買入/賣出訊號
  ↓
依信心分數排序，執行虛擬買賣
  ↓
產生 index.html → push 到 GitHub → Vercel 自動更新
手機開 Vercel 網址查看結果
```

---

## 設定（修改 run_daily.py 頂部）

```python
INITIAL_CAPITAL  = 200_000   # 初始本金（元）← 可自行修改
MAX_POSITIONS    = 5         # 最多同時持有幾支股票
BUY_PER_STOCK    = 30_000    # 每次買入金額（元）
SELL_STOP_LOSS   = -0.08     # 停損 -8%
SELL_TAKE_PROFIT =  0.15     # 停利 +15%

# 篩選條件
MIN_PRICE        = 10.0      # 最低收盤價（元）
MIN_VOLUME_K     = 1000      # 最低成交量（張）
```

---

## 本機手動執行

```bash
# 安裝套件（只需做一次）
pip install -r requirements.txt

# 執行（約 10~20 分鐘）
python run_daily.py
```

---

## 策略說明

| 指標 | 買入條件 | 賣出條件 |
|------|---------|---------|
| 均線交叉 | MA5 上穿 MA20（黃金交叉）| MA5 下穿 MA20（死亡交叉）|
| RSI | RSI < 30（超賣）| RSI > 70（超買）|
| 布林通道 | 股價碰觸下軌 | 股價碰觸上軌 |
| 成交量 | 配合訊號放量確認 | 配合訊號放量確認 |

- 停損：持倉虧損超過 **-8%** 自動賣出
- 停利：持倉獲利超過 **+15%** 自動賣出

---

## 資料來源

| 用途 | 來源 |
|------|------|
| 股票代號名單 | TWSE OpenAPI（免費）|
| 歷史日線、今日收盤價 | Yahoo Finance / yfinance（免費）|

統一使用 yfinance 取得價格，避免多來源時間差問題。
yfinance 通常收盤後 1~2 小時更新，排程設隔天早上 05:00 執行。

---

## GitHub Actions 排程

```yaml
- cron: '0 9 * * 1-5'
# 台灣時間週一到週五 17:00 = UTC 09:00
```

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `run_daily.py` | 主程式 |
| `data_fetcher.py` | 資料抓取 |
| `strategy.py` | 技術指標與訊號判斷 |
| `portfolio.py` | 虛擬帳戶與損益計算 |
| `dashboard.html` | 儀表板模板 |
| `data/portfolio.json` | 帳戶資料（交易記錄）|
| `index.html` | Vercel 首頁（每次執行自動產生）|

---

## 重置帳戶

刪掉 `data/portfolio.json`，下次執行自動重建，本金重置為設定值。

---

⚠️ **模擬系統，不構成投資建議。**
