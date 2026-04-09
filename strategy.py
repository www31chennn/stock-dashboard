"""
策略引擎：技術指標計算 + 買賣訊號判斷
策略組合：
  1. 均線交叉 (MA5 / MA20)
  2. RSI 超買超賣
  3. 布林通道突破
  4. 成交量放大確認
"""
import pandas as pd
import numpy as np


# ── 技術指標計算 ──────────────────────────────────────────────────────────

def calc_ma(df: pd.DataFrame, windows=[5, 10, 20, 60]) -> pd.DataFrame:
    for w in windows:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
    return df


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_bollinger(df: pd.DataFrame, window: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    df["bb_mid"] = df["close"].rolling(window).mean()
    rolling_std = df["close"].rolling(window).std()
    df["bb_upper"] = df["bb_mid"] + std_dev * rolling_std
    df["bb_lower"] = df["bb_mid"] - std_dev * rolling_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def calc_volume_signal(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df["vol_ma"] = df["volume"].rolling(window).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]  # > 1.5 代表放量
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算全部技術指標，至少需要 60 根 K 棒"""
    df = df.sort_values("date").reset_index(drop=True)
    df = calc_ma(df)
    df = calc_rsi(df)
    df = calc_bollinger(df)
    df = calc_volume_signal(df)
    return df


# ── 訊號判斷 ──────────────────────────────────────────────────────────────

def get_signal(df: pd.DataFrame) -> dict:
    """
    輸入：含技術指標的歷史日線 DataFrame
    輸出：{
        "action": "BUY" | "SELL" | "HOLD",
        "reason": [...],
        "confidence": 0~100,
        "price": 最新收盤價,
        "rsi": float,
        "ma5": float, "ma20": float,
    }
    """
    if len(df) < 25:
        return {"action": "HOLD", "reason": ["資料不足"], "confidence": 0}

    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    buy_scores  = []
    sell_scores = []
    reasons     = []

    # ── 訊號 1：MA5 上穿 MA20（黃金交叉）
    if prev["ma5"] < prev["ma20"] and latest["ma5"] > latest["ma20"]:
        buy_scores.append(35)
        reasons.append("MA5 上穿 MA20（黃金交叉）")
    # ── 訊號 1b：MA5 下穿 MA20（死亡交叉）
    elif prev["ma5"] > prev["ma20"] and latest["ma5"] < latest["ma20"]:
        sell_scores.append(35)
        reasons.append("MA5 下穿 MA20（死亡交叉）")

    # ── 訊號 2：RSI
    rsi = latest["rsi"]
    if rsi < 30:
        buy_scores.append(30)
        reasons.append(f"RSI={rsi:.1f} 超賣（< 30）")
    elif rsi < 40:
        buy_scores.append(15)
        reasons.append(f"RSI={rsi:.1f} 偏低（< 40）")
    elif rsi > 70:
        sell_scores.append(30)
        reasons.append(f"RSI={rsi:.1f} 超買（> 70）")
    elif rsi > 60:
        sell_scores.append(15)
        reasons.append(f"RSI={rsi:.1f} 偏高（> 60）")

    # ── 訊號 3：布林通道
    close = latest["close"]
    if close <= latest["bb_lower"]:
        buy_scores.append(20)
        reasons.append("股價碰觸布林下軌（超賣反彈訊號）")
    elif close >= latest["bb_upper"]:
        sell_scores.append(20)
        reasons.append("股價碰觸布林上軌（超買回落訊號）")

    # ── 訊號 4：成交量確認（放量才有說服力）
    vol_ratio = latest.get("vol_ratio", 1.0)
    if not pd.isna(vol_ratio) and vol_ratio > 1.5:
        if buy_scores:
            buy_scores.append(15)
            reasons.append(f"成交量放大 {vol_ratio:.1f}x（買訊確認）")
        elif sell_scores:
            sell_scores.append(15)
            reasons.append(f"成交量放大 {vol_ratio:.1f}x（賣訊確認）")

    # ── 綜合判斷
    total_buy  = sum(buy_scores)
    total_sell = sum(sell_scores)

    if total_buy >= 35 and total_buy > total_sell:
        action     = "BUY"
        confidence = min(total_buy, 100)
    elif total_sell >= 35 and total_sell > total_buy:
        action     = "SELL"
        confidence = min(total_sell, 100)
    else:
        action     = "HOLD"
        confidence = 0

    return {
        "action":     action,
        "reason":     reasons if reasons else ["無明確訊號"],
        "confidence": confidence,
        "price":      float(close),
        "rsi":        float(rsi) if not pd.isna(rsi) else None,
        "ma5":        float(latest["ma5"])  if not pd.isna(latest["ma5"])  else None,
        "ma20":       float(latest["ma20"]) if not pd.isna(latest["ma20"]) else None,
        "bb_upper":   float(latest["bb_upper"]) if not pd.isna(latest["bb_upper"]) else None,
        "bb_lower":   float(latest["bb_lower"]) if not pd.isna(latest["bb_lower"]) else None,
        "vol_ratio":  float(vol_ratio) if not pd.isna(vol_ratio) else None,
    }
