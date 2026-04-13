"""
每日執行腳本 v2
資料來源統一使用 yfinance，排除多來源時間差問題。
流程：
  1. TWSE 抓股票名單（代號+名稱）
  2. yfinance 批次抓全市場資料並篩選
  3. 對篩選後的股票計算技術指標、找訊號
  4. 執行虛擬買賣
  5. 更新帳戶快照、產生報告
"""
import os, json
import pandas as pd
from datetime import datetime

from data_fetcher import (fetch_twse_stock_list, fetch_and_filter_stocks,
                           fetch_history, get_name_map)
from strategy    import add_all_indicators, get_signal
from portfolio   import (load_portfolio, save_portfolio,
                          execute_buy, execute_sell, take_daily_snapshot)

# ══════════════════════════════════════════════════════════════════
# ★ 設定區（可自行調整）
# ══════════════════════════════════════════════════════════════════
INITIAL_CAPITAL  = 200_000   # 初始本金（元），可自行修改
MAX_POSITIONS    = 5         # 最多同時持有幾支股票
BUY_PER_STOCK    = 30_000    # 每次買入金額（元）
SELL_STOP_LOSS   = -0.08     # 停損 -8%
SELL_TAKE_PROFIT =  0.15     # 停利 +15%

# 篩選條件
MIN_PRICE        = 10.0      # 最低收盤價（元）
MIN_VOLUME_K     = 1000      # 最低成交量（張）
PERIOD_DAYS      = 90        # 歷史資料天數
FETCH_DELAY      = 0.5       # 每支股票抓取間隔（秒）
# ══════════════════════════════════════════════════════════════════

DATA_DIR   = os.path.join(os.path.dirname(__file__), "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(DATA_DIR,   exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ── 產生獨立 HTML ─────────────────────────────────────────────────
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

    # 本地用
    out_path = os.path.join(REPORT_DIR, "dashboard_today.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Vercel 用
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  OK 本地儀表板：{out_path}")
    print(f"  OK Vercel 首頁：{index_path}")


# ── 每日主流程 ────────────────────────────────────────────────────
def run_daily():
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  台股模擬交易系統 v2  {today}")
    print(f"  本金設定：{INITIAL_CAPITAL:,.0f} 元")
    print(f"{'='*60}")

    # ── Step 1: 取得股票名單 ──────────────────────────────────────
    print("\n[1/5] 取得股票名單...")
    stock_list = fetch_twse_stock_list()
    name_map   = get_name_map(stock_list)
    if stock_list.empty:
        print("  ⚠ 無法取得股票名單，使用空清單")

    # ── Step 2: 全市場篩選 + 抓歷史資料 ──────────────────────────
    print(f"\n[2/5] 全市場篩選（收盤價>{MIN_PRICE}元、成交量>{MIN_VOLUME_K}張）...")
    history_data = fetch_and_filter_stocks(
        stock_list,
        min_price    = MIN_PRICE,
        min_volume_k = MIN_VOLUME_K,
        period_days  = PERIOD_DAYS,
        delay        = FETCH_DELAY,
    )
    print(f"  → 共 {len(history_data)} 支股票進入技術分析")

    # ── Step 3: 計算技術指標、找訊號 ─────────────────────────────
    print(f"\n[3/5] 計算技術指標...")
    signals = {}
    for sid, df in history_data.items():
        df  = add_all_indicators(df)
        sig = get_signal(df)
        sig["name"] = name_map.get(sid, sid)
        signals[sid] = sig

    buy_signals  = {s: v for s, v in signals.items() if v["action"] == "BUY"}
    sell_signals = {s: v for s, v in signals.items() if v["action"] == "SELL"}
    print(f"  → 買入訊號: {len(buy_signals)} 支　賣出訊號: {len(sell_signals)} 支")

    # ── Step 4: 執行虛擬交易 ──────────────────────────────────────
    print("\n[4/5] 執行虛擬交易...")
    portfolio    = load_portfolio(INITIAL_CAPITAL)
    trades_today = []

    # 更新持倉現價（從 signals 或重新抓）
    price_map = {sid: v["price"] for sid, v in signals.items() if v.get("price")}

    # 補抓持倉中但不在 signals 裡的股票現價
    for sid in list(portfolio["positions"].keys()):
        if sid not in price_map:
            df = fetch_history(sid, 10)
            if not df.empty:
                price_map[sid] = float(df.iloc[-1]["close"])

    # 先處理賣出
    for sid, pos in list(portfolio["positions"].items()):
        market_price = price_map.get(sid)
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
                print(f"  賣出 {sid} {pos['name']} @ {market_price:.1f}"
                      f"  損益: {rec['pnl']:+,.0f} 元  原因: {reason}")

    # 再處理買入（依信心分數排序）
    sorted_buys = sorted(buy_signals.items(), key=lambda x: x[1]["confidence"], reverse=True)
    for sid, sig in sorted_buys:
        if sid in portfolio["positions"]:
            continue
        if len(portfolio["positions"]) >= MAX_POSITIONS:
            break
        price = sig.get("price")
        if not price or price <= 0:
            continue

        lots   = max(1, int(BUY_PER_STOCK / (price * 1000)))
        shares = lots * 1000
        name   = name_map.get(sid, sid)

        result = execute_buy(portfolio, sid, name, price, shares)
        if result["success"]:
            rec = result["record"]
            rec["trigger"]    = "技術訊號：" + "、".join(sig["reason"])
            rec["confidence"] = sig["confidence"]
            trades_today.append(rec)
            print(f"  買入 {sid} {name} @ {price:.1f}"
                  f"  {shares:,}股  信心:{sig['confidence']}%")

    if not trades_today:
        print("  今日無交易（持倉不變）")

    # ── Step 5: 快照 + 報告 ───────────────────────────────────────
    print("\n[5/5] 產出報告...")
    daily_snap = take_daily_snapshot(portfolio, price_map)

    # 只輸出有買賣訊號的股票到報告（避免資料量太大）
    top_signals = {}
    # 全部 BUY 和 SELL
    for sid, v in signals.items():
        if v["action"] in ("BUY", "SELL"):
            top_signals[sid] = v
    # 加上目前持倉
    for sid in portfolio["positions"]:
        if sid in signals:
            top_signals[sid] = signals[sid]
    # 補上信心最高的 HOLD（最多 20 支）
    hold_sigs = sorted(
        [(s, v) for s, v in signals.items() if v["action"] == "HOLD"],
        key=lambda x: x[1].get("rsi", 50) or 50
    )[:20]
    for sid, v in hold_sigs:
        top_signals[sid] = v

    report = {
        "generated_at":   today,
        "summary":        daily_snap,
        "trades_today":   trades_today,
        "screened_total": len(history_data),
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
                "name":       v.get("name", sid),
            }
            for sid, v in top_signals.items()
        },
        "portfolio": {
            "cash":      portfolio["cash"],
            "positions": portfolio["positions"],
            "daily_log": portfolio["daily_log"],
            "trade_log": portfolio["trade_log"][-50:],
        },
        "ai_review": "no_api_key",
        "settings": {
            "initial_capital":  INITIAL_CAPITAL,
            "max_positions":    MAX_POSITIONS,
            "buy_per_stock":    BUY_PER_STOCK,
            "sell_stop_loss":   SELL_STOP_LOSS,
            "sell_take_profit": SELL_TAKE_PROFIT,
            "min_price":        MIN_PRICE,
            "min_volume_k":     MIN_VOLUME_K,
        }
    }

    report_path = os.path.join(REPORT_DIR, "latest_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    dated_path = os.path.join(REPORT_DIR, f"report_{datetime.now().strftime('%Y%m%d')}.json")
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    _generate_standalone_html(report)

    # 終端機摘要
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
    print(f"  篩選結果：從全市場篩出 {len(history_data)} 支股票分析")
    print(f"{'='*60}\n")
    return report


if __name__ == "__main__":
    run_daily()
