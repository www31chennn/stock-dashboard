"""
台股資料抓取模組 v2
統一使用 yfinance 作為資料來源，避免多來源時間差問題。
TWSE API 只用來取得股票名稱清單，不取價格。
"""
import requests
import yfinance as yf
import pandas as pd
import time
import os
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ── 1. 從 TWSE 取得全市場股票代號和名稱（不取價格）────────────────────────
def fetch_twse_stock_list() -> pd.DataFrame:
    """
    只取股票代號和名稱，用來做第一層名單
    回傳 DataFrame: stock_id, name
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data)
        df = df.rename(columns={"Code": "stock_id", "Name": "name"})

        # 只取代號和名稱，過濾掉非純數字代號（ETF、權證等）
        df = df[["stock_id", "name"]].copy()
        df = df[df["stock_id"].str.match(r"^\d{4}$")]  # 只要4位數純數字
        df = df.reset_index(drop=True)
        print(f"[TWSE] 取得 {len(df)} 支股票名單")
        return df
    except Exception as e:
        print(f"[TWSE] 名單取得失敗: {e}")
        return pd.DataFrame()


# ── 2. 用 yfinance 批次抓股票資料（含篩選）──────────────────────────────────
def fetch_and_filter_stocks(
    stock_list: pd.DataFrame,
    min_price: float = 10.0,
    min_volume_k: int = 1000,
    period_days: int = 90,
    batch_size: int = 50,
    delay: float = 0.5,
) -> dict:
    """
    從股票名單中批次抓取 yfinance 資料，並做基本篩選：
      - 收盤價 > min_price 元
      - 成交量 > min_volume_k 張（千股）
      - 至少有 25 天有效資料

    回傳 dict: {stock_id: DataFrame（含 90 天日線）}
    """
    if stock_list.empty:
        return {}

    all_ids = stock_list["stock_id"].tolist()
    end   = datetime.today()
    start = end - timedelta(days=period_days + 15)

    result     = {}
    passed     = 0
    filtered   = 0
    failed     = 0
    total      = len(all_ids)

    print(f"[篩選] 開始處理 {total} 支股票（條件：收盤價>{min_price}元、成交量>{min_volume_k}張）")

    for i, sid in enumerate(all_ids, 1):
        try:
            ticker = f"{sid}.TW"
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),  # +1天確保抓到今日
                interval="1d",
                progress=False,
                auto_adjust=True,
            )

            if df.empty or len(df) < 25:
                failed += 1
                continue

            # 整理欄位
            df = df[["Open","High","Low","Close","Volume"]].copy()
            df.columns = ["open","high","low","close","volume"]
            df.index.name = "date"
            df = df.reset_index()
            df["stock_id"] = sid

            # 取最新一筆做篩選
            latest = df.iloc[-1]
            close  = float(latest["close"])
            volume = float(latest["volume"]) / 1000  # 換算成張

            if close < min_price:
                filtered += 1
                continue
            if volume < min_volume_k:
                filtered += 1
                continue

            result[sid] = df.tail(period_days).reset_index(drop=True)
            passed += 1

        except Exception:
            failed += 1

        # 進度顯示
        if i % batch_size == 0 or i == total:
            print(f"  進度 {i}/{total}：通過 {passed} 支，篩掉 {filtered} 支，失敗 {failed} 支")

        time.sleep(delay)

    print(f"[篩選完成] 共 {passed} 支股票進入技術分析")
    return result


# ── 3. 單支股票歷史（持倉更新用）────────────────────────────────────────────
def fetch_history(stock_id: str, period_days: int = 90) -> pd.DataFrame:
    """抓單支股票歷史，主要用來更新持倉現價"""
    ticker = f"{stock_id}.TW"
    end   = datetime.today()
    start = end - timedelta(days=period_days + 15)
    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),  # +1天確保抓到今日
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index.name = "date"
        df = df.reset_index()
        df["stock_id"] = stock_id
        return df.tail(period_days).reset_index(drop=True)
    except Exception as e:
        print(f"[yfinance] {stock_id} 失敗: {e}")
        return pd.DataFrame()


# ── 4. 取股票名稱對照表 ───────────────────────────────────────────────────────
def get_name_map(stock_list: pd.DataFrame) -> dict:
    """回傳 {stock_id: name} 的對照表"""
    if stock_list.empty:
        return {}
    return dict(zip(stock_list["stock_id"], stock_list["name"]))
