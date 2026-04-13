"""
虛擬帳戶模組 v2
- 本金可調整（由外部傳入）
- 買賣執行（模擬收盤價成交）
- 持倉追蹤
- 盈虧計算（含手續費 0.1425%，證交稅 0.3%）
"""
import json
import os
from datetime import datetime

BROKERAGE_FEE_RATE = 0.001425
TAX_RATE           = 0.003
MIN_FEE            = 20

DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")


def load_portfolio(initial_capital: float = 200_000.0) -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cash":      initial_capital,
        "initial":   initial_capital,
        "positions": {},
        "trade_log": [],
        "daily_log": [],
    }


def save_portfolio(portfolio: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


def calc_buy_cost(price: float, shares: int) -> dict:
    amount = price * shares
    fee    = max(round(amount * BROKERAGE_FEE_RATE), MIN_FEE)
    return {"amount": amount, "fee": fee, "total": amount + fee}


def calc_sell_proceeds(price: float, shares: int) -> dict:
    amount = price * shares
    fee    = max(round(amount * BROKERAGE_FEE_RATE), MIN_FEE)
    tax    = round(amount * TAX_RATE)
    return {"amount": amount, "fee": fee, "tax": tax, "net": amount - fee - tax}


def execute_buy(portfolio: dict, stock_id: str, name: str,
                price: float, shares: int = 1000) -> dict:
    cost = calc_buy_cost(price, shares)
    if portfolio["cash"] < cost["total"]:
        return {"success": False, "reason": "資金不足"}

    portfolio["cash"] -= cost["total"]
    pos = portfolio["positions"].get(stock_id)
    if pos:
        total_shares = pos["shares"] + shares
        total_cost   = pos["avg_cost"] * pos["shares"] + price * shares
        pos["shares"]   = total_shares
        pos["avg_cost"] = round(total_cost / total_shares, 2)
        pos["name"]     = name
    else:
        portfolio["positions"][stock_id] = {
            "shares":   shares,
            "avg_cost": price,
            "name":     name,
        }

    record = {
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action":   "BUY",
        "stock_id": stock_id,
        "name":     name,
        "price":    price,
        "shares":   shares,
        "amount":   cost["amount"],
        "fee":      cost["fee"],
        "total":    cost["total"],
    }
    portfolio["trade_log"].append(record)
    save_portfolio(portfolio)
    return {"success": True, "record": record}


def execute_sell(portfolio: dict, stock_id: str,
                 price: float, shares: int = None) -> dict:
    pos = portfolio["positions"].get(stock_id)
    if not pos:
        return {"success": False, "reason": "無持倉"}

    if shares is None:
        shares = pos["shares"]
    shares   = min(shares, pos["shares"])
    proceeds = calc_sell_proceeds(price, shares)
    avg_cost = pos["avg_cost"]
    pnl      = proceeds["net"] - avg_cost * shares
    pnl_pct  = pnl / (avg_cost * shares) * 100

    portfolio["cash"] += proceeds["net"]
    if shares >= pos["shares"]:
        del portfolio["positions"][stock_id]
    else:
        pos["shares"] -= shares

    record = {
        "datetime":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action":    "SELL",
        "stock_id":  stock_id,
        "name":      pos["name"],
        "price":     price,
        "shares":    shares,
        "amount":    proceeds["amount"],
        "fee":       proceeds["fee"],
        "tax":       proceeds["tax"],
        "net":       proceeds["net"],
        "avg_cost":  avg_cost,
        "pnl":       round(pnl, 0),
        "pnl_pct":   round(pnl_pct, 2),
    }
    portfolio["trade_log"].append(record)
    save_portfolio(portfolio)
    return {"success": True, "record": record}


def take_daily_snapshot(portfolio: dict, price_map: dict) -> dict:
    total_market_value = 0.0
    positions_detail   = []

    for sid, pos in portfolio["positions"].items():
        market_price   = price_map.get(sid, pos["avg_cost"])
        market_value   = market_price * pos["shares"]
        unrealized     = market_value - pos["avg_cost"] * pos["shares"]
        unrealized_pct = unrealized / (pos["avg_cost"] * pos["shares"]) * 100
        total_market_value += market_value
        positions_detail.append({
            "stock_id":       sid,
            "name":           pos["name"],
            "shares":         pos["shares"],
            "avg_cost":       pos["avg_cost"],
            "market_price":   market_price,
            "market_value":   round(market_value, 0),
            "unrealized":     round(unrealized, 0),
            "unrealized_pct": round(unrealized_pct, 2),
        })

    total_assets  = portfolio["cash"] + total_market_value
    total_pnl     = total_assets - portfolio["initial"]
    total_pnl_pct = total_pnl / portfolio["initial"] * 100

    snapshot = {
        "date":             datetime.now().strftime("%Y-%m-%d"),
        "cash":             round(portfolio["cash"], 0),
        "market_value":     round(total_market_value, 0),
        "total_assets":     round(total_assets, 0),
        "total_pnl":        round(total_pnl, 0),
        "total_pnl_pct":    round(total_pnl_pct, 2),
        "positions_detail": positions_detail,
    }

    existing = [d for d in portfolio["daily_log"] if d["date"] != snapshot["date"]]
    existing.append(snapshot)
    portfolio["daily_log"] = existing
    save_portfolio(portfolio)
    return snapshot
