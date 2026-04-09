"""
台股資料抓取模組
使用 TWSE 官方免費 API + yfinance 作為備援
"""
import requests
import yfinance as yf
import pandas as pd
import json
import time
import os
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── 1. 取得今日全市場收盤資料（TWSE OpenAPI，免費） ─────────────────────
def fetch_twse_all_stocks():
    """從證交所抓今日所有上市股票收盤行情"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data)
        # 欄位：Code, Name, TradeVolume, TradeValue, OpeningPrice,
        #        HighestPrice, LowestPrice, ClosingPrice, Change, Transaction
        numeric_cols = ["OpeningPrice","HighestPrice","LowestPrice","ClosingPrice","Change","TradeVolume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",",""), errors="coerce")
        df = df.rename(columns={
            "Code": "stock_id",
            "Name": "name",
            "OpeningPrice": "open",
            "HighestPrice": "high",
            "LowestPrice": "low",
            "ClosingPrice": "close",
            "Change": "change",
            "TradeVolume": "volume",
        })
        df["date"] = datetime.today().strftime("%Y-%m-%d")
        print(f"[TWSE] 取得 {len(df)} 筆股票資料")
        return df
    except Exception as e:
        print(f"[TWSE] 失敗: {e}，改用 yfinance...")
        return None


# ── 2. 取得歷史日線資料（yfinance，免費，供計算技術指標） ─────────────────
def fetch_history(stock_id: str, period_days: int = 60) -> pd.DataFrame:
    """
    抓單支股票近 N 天日線，用來算均線/RSI
    stock_id: '2330' (不含 .TW)
    """
    ticker = f"{stock_id}.TW"
    end = datetime.today()
    start = end - timedelta(days=period_days + 10)  # 多抓幾天以防假日
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index.name = "date"
        df = df.reset_index()
        df["stock_id"] = stock_id
        return df.tail(period_days)
    except Exception as e:
        print(f"[yfinance] {stock_id} 失敗: {e}")
        return pd.DataFrame()


# ── 3. 批次抓多支股票歷史（用於系統每日更新候選池） ──────────────────────
def fetch_history_batch(stock_ids: list, period_days: int = 60) -> dict:
    """回傳 dict: {stock_id: DataFrame}"""
    result = {}
    for sid in stock_ids:
        df = fetch_history(sid, period_days)
        if not df.empty:
            result[sid] = df
        time.sleep(0.3)  # 避免被擋
    return result


# ── 4. 存盤 / 讀盤 ────────────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def save_today_snapshot(df: pd.DataFrame):
    today = datetime.today().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"snapshot_{today}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[儲存] {path}")

def load_today_snapshot() -> pd.DataFrame:
    today = datetime.today().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"snapshot_{today}.csv")
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()
