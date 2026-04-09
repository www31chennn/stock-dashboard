"""
每日執行腳本 ── 每天收盤後跑這個就好
"""
import os, json, time
import pandas as pd
from datetime import datetime

from data_fetcher import fetch_twse_all_stocks, fetch_history_batch, save_today_snapshot
from strategy    import add_all_indicators, get_signal
from portfolio   import (load_portfolio, save_portfolio,
                          execute_buy, execute_sell, take_daily_snapshot)

MAX_POSITIONS    = 5
BUY_PER_STOCK    = 30_000
SELL_STOP_LOSS   = -0.08
SELL_TAKE_PROFIT =  0.15

CANDIDATE_STOCKS = [
    "2330","2317","2454","2382","2308",
    "2881","2882","2886","2884","2891",
    "1301","1303","6505","2002","1326",
    "2412","3008","2357","4938","3711",
    "2603","2609","2615","2618","5880",
]

DATA_DIR   = os.path.join(os.path.dirname(__file__), "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(DATA_DIR,   exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ── AI 策略檢討（Python 端呼叫，結果寫入報告）────────────────────────────
def run_ai_review(report: dict) -> str:
    """呼叫 Claude API 做策略分析，回傳分析文字"""
    try:
        import anthropic
    except ImportError:
        return "（未安裝 anthropic 套件，請執行：pip install anthropic）"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "（未設定 ANTHROPIC_API_KEY 環境變數，請參考 README 設定）"

    trades    = report["portfolio"]["trade_log"]
    daily_log = report["portfolio"]["daily_log"]
    sells     = [t for t in trades if t["action"] == "SELL" and t.get("pnl") is not None]
    wins      = [t for t in sells if t["pnl"] > 0]
    losses    = [t for t in sells if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in sells)
    win_rate  = f"{len(wins)/len(sells)*100:.1f}%" if sells else "尚無賣出記錄"

    summary = {
        "跑了幾天": len(daily_log),
        "累計損益": f"{report['summary']['total_pnl']:+,.0f} 元（{report['summary']['total_pnl_pct']:+.2f}%）",
        "勝率": win_rate,
        "獲利次數": len(wins),
        "虧損次數": len(losses),
        "已實現總損益": f"{total_pnl:+,.0f} 元",
        "目前持倉": [
            f"{p['stock_id']} {p['name']} 未實現{p['unrealized_pct']:+.1f}%"
            for p in report["summary"].get("positions_detail", [])
        ],
        "最近10筆交易": [
            f"{t['datetime'][:10]} {'買入' if t['action']=='BUY' else '賣出'} "
            f"{t['stock_id']} ${t['price']:.1f}"
            + (f" 損益{t['pnl']:+,.0f}" if t.get("pnl") is not None else "")
            for t in trades[-10:]
        ]
    }

    prompt = f"""你是一個台股量化投資顧問，正在幫使用者檢視他的模擬交易系統。

系統策略：MA5/MA20 均線交叉 + RSI 超買超賣 + 布林通道 + 成交量確認
參數設定：停損 -8%、停利 +15%、最多同時持 5 支、每次買入約 3 萬元

以下是這套系統目前的績效數據：
{json.dumps(summary, ensure_ascii=False, indent=2)}

請用繁體中文回答，語言要白話易懂，盡量避免術語（若一定要用請加說明）。
格式：
1. 【整體表現】目前狀況如何（2-3句）
2. 【發現的問題】有什麼地方需要注意（如果有的話）
3. 【建議調整】策略有沒有需要改進的地方
4. 【一句話結論】給使用者最簡短的建議

注意：如果跑的天數太少（少於 10 天），請說明需要更多時間才能評估。"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"（AI 分析失敗：{e}）"


# ── 產生獨立 HTML ─────────────────────────────────────────────────────────
def _generate_standalone_html(report: dict):
    template_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.exists(template_path):
        print("  找不到 dashboard.html，跳過")
        return
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    data_js = (
        "<script>\n"
        "window.__REPORT__ = " + json.dumps(report, ensure_ascii=False) + ";\n"
        "</script>\n"
    )
    html = html.replace("<script>", data_js + "<script>", 1)

    inject = (
        "<script>\n"
        "async function loadReport() {\n"
        "  var report = window.__REPORT__;\n"
        "  if (!report) return;\n"
        "  _report = report;\n"
        "  document.getElementById('error-banner').style.display = 'none';\n"
        "  renderAll(report);\n"
        "}\n"
        "</script>\n"
    )
    html = html.replace("</body>", inject + "</body>")

    # 輸出到 reports/dashboard_today.html（本地用）
    out_path = os.path.join(REPORT_DIR, "dashboard_today.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 同時輸出到根目錄的 index.html（Vercel 用）
    root_dir = os.path.dirname(__file__)
    index_path = os.path.join(root_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  OK 本地儀表板：{out_path}")
    print(f"  OK Vercel 首頁：{index_path}")
    print(f"     git push 後 Vercel 會自動更新！")


# ── 每日主流程 ────────────────────────────────────────────────────────────
def run_daily():
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}\n  台股模擬交易系統  {today}\n{'='*60}")

    print("\n[1/5] 抓取市場資料...")
    snapshot_df = fetch_twse_all_stocks()
    if snapshot_df is not None and not snapshot_df.empty:
        save_today_snapshot(snapshot_df)
        price_map = dict(zip(
            snapshot_df["stock_id"].astype(str),
            pd.to_numeric(snapshot_df["close"], errors="coerce")
        ))
    else:
        print("  無法取得今日快照")
        price_map = {}

    print(f"\n[2/5] 分析 {len(CANDIDATE_STOCKS)} 支候選股票...")
    history_data = fetch_history_batch(CANDIDATE_STOCKS, period_days=90)
    signals = {}
    for sid, df in history_data.items():
        df  = add_all_indicators(df)
        sig = get_signal(df)
        if sid in price_map and not pd.isna(price_map[sid]):
            sig["price"] = price_map[sid]
        signals[sid] = sig
    buy_signals  = {s: v for s, v in signals.items() if v["action"] == "BUY"}
    sell_signals = {s: v for s, v in signals.items() if v["action"] == "SELL"}
    print(f"  買入訊號: {len(buy_signals)} 支　賣出訊號: {len(sell_signals)} 支")

    print("\n[3/5] 執行虛擬交易...")
    portfolio    = load_portfolio()
    trades_today = []

    for sid, pos in list(portfolio["positions"].items()):
        market_price = signals.get(sid, {}).get("price") or price_map.get(sid)
        if not market_price:
            continue
        pnl_pct = (market_price - pos["avg_cost"]) / pos["avg_cost"]
        reason = None
        if pnl_pct <= SELL_STOP_LOSS:
            reason = f"停損觸發（跌幅 {pnl_pct*100:.1f}%）"
        elif pnl_pct >= SELL_TAKE_PROFIT:
            reason = f"停利觸發（漲幅 {pnl_pct*100:.1f}%）"
        elif sid in sell_signals:
            reason = "技術訊號：" + "、".join(sell_signals[sid]["reason"])
        if reason:
            result = execute_sell(portfolio, sid, market_price)
            if result["success"]:
                rec = result["record"]
                rec["trigger"] = reason
                trades_today.append(rec)
                print(f"  賣出 {sid} {pos['name']} @ {market_price:.1f}  損益: {rec['pnl']:+,.0f} 元")

    sorted_buys = sorted(buy_signals.items(), key=lambda x: x[1]["confidence"], reverse=True)
    for sid, sig in sorted_buys:
        if sid in portfolio["positions"]:
            continue
        if len(portfolio["positions"]) >= MAX_POSITIONS:
            break
        price = sig["price"]
        if not price or price <= 0:
            continue
        lots   = max(1, int(BUY_PER_STOCK / (price * 1000)))
        shares = lots * 1000
        name   = sid
        if snapshot_df is not None and not snapshot_df.empty:
            row = snapshot_df[snapshot_df["stock_id"] == sid]
            if not row.empty and "name" in row.columns:
                name = row.iloc[0]["name"]
        result = execute_buy(portfolio, sid, name, price, shares)
        if result["success"]:
            rec = result["record"]
            rec["trigger"]    = "技術訊號：" + "、".join(sig["reason"])
            rec["confidence"] = sig["confidence"]
            trades_today.append(rec)
            print(f"  買入 {sid} {name} @ {price:.1f}  {shares:,} 股  信心: {sig['confidence']}%")

    if not trades_today:
        print("  今日無交易（持倉不變）")

    print("\n[4/5] 計算帳戶狀態...")
    live_price_map = {}
    for sid in list(portfolio["positions"].keys()):
        p = signals.get(sid, {}).get("price") or price_map.get(sid)
        if p:
            live_price_map[sid] = p
    daily_snap = take_daily_snapshot(portfolio, live_price_map)

    print("\n[5/5] 產出報告...")
    report = {
        "generated_at": today,
        "summary":      daily_snap,
        "trades_today": trades_today,
        "all_signals": {
            sid: {
                "action":     v["action"],
                "confidence": v["confidence"],
                "price":      v["price"],
                "rsi":        v["rsi"],
                "ma5":        v.get("ma5"),
                "ma20":       v.get("ma20"),
                "bb_upper":   v.get("bb_upper"),
                "bb_lower":   v.get("bb_lower"),
                "vol_ratio":  v.get("vol_ratio"),
                "reason":     v["reason"],
            }
            for sid, v in signals.items()
        },
        "portfolio": {
            "cash":      portfolio["cash"],
            "positions": portfolio["positions"],
            "daily_log": portfolio["daily_log"],
            "trade_log": portfolio["trade_log"][-50:],
        },
        "ai_review": None  # 預設空，有設 API key 才會有內容
    }

    # AI 策略分析（如果有設定 API key）
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("  正在請 AI 分析策略...")
        review = run_ai_review(report)
        report["ai_review"] = review
        print("  AI 分析完成")
    else:
        report["ai_review"] = "no_api_key"

    report_path = os.path.join(REPORT_DIR, "latest_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    dated_path = os.path.join(REPORT_DIR, f"report_{datetime.now().strftime('%Y%m%d')}.json")
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    _generate_standalone_html(report)

    print(f"\n{'─'*60}")
    print(f"  帳戶總覽")
    print(f"  現金:      {daily_snap['cash']:>12,.0f} 元")
    print(f"  持股市值:  {daily_snap['market_value']:>12,.0f} 元")
    print(f"  總資產:    {daily_snap['total_assets']:>12,.0f} 元")
    sym = "+" if daily_snap["total_pnl"] >= 0 else ""
    print(f"  累計損益:  {sym}{daily_snap['total_pnl']:,.0f} 元  ({sym}{daily_snap['total_pnl_pct']:.2f}%)")
    print(f"{'─'*60}")
    if daily_snap["positions_detail"]:
        print("  持倉明細:")
        for p in daily_snap["positions_detail"]:
            s = "+" if p["unrealized"] >= 0 else ""
            print(f"    {p['stock_id']} {p['name']:<6}  {p['shares']:>6,}股  "
                  f"成本:{p['avg_cost']:.1f}  現價:{p['market_price']:.1f}  "
                  f"損益:{s}{p['unrealized']:,.0f}元 ({s}{p['unrealized_pct']:.1f}%)")
    print(f"{'='*60}\n")
    return report


if __name__ == "__main__":
    run_daily()
