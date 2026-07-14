"""
daily_summary.py — Daily Summary (Section 9)
──────────────────────────────────────────────
Generates and sends the daily summary Telegram message at 3:30 PM.
"""

import logging
from datetime import datetime
import pytz

from config import Config
from database import SessionLocal, Trade, get_ist_date
from telegram_alert import alert_daily_summary

IST = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)


def generate_daily_summary(
    execution_engine,
    pipeline_stats: dict = None,
    user_id: int = None,
) -> str:
    """
    Generate and send daily summary at 3:30 PM.
    
    Args:
        execution_engine: For fetching Nifty closing price and VIX via REST
        pipeline_stats: Dict with scan counts, signal counts, etc.
        user_id: Optional user_id filter
        
    Returns:
        The summary string.
    """
    today = get_ist_date()
    date_str = today.strftime("%d-%b-%Y")
    stats = pipeline_stats or {}

    # Fetch Nifty closing price and VIX via REST API (WebSocket disconnected at 3:25 PM)
    nifty_close = 0.0
    nifty_change_pct = 0.0
    vix_close = 0.0
    
    try:
        nifty_key = "NSE_INDEX|Nifty 50"
        vix_key = "NSE_INDEX|India VIX"
        quotes = execution_engine.get_market_quotes([nifty_key, vix_key])
        
        nifty_quote = quotes.get(nifty_key, {})
        if nifty_quote:
            nifty_close = float(nifty_quote.get('last_price', 0))
            net_change = float(nifty_quote.get('net_change', 0))
            if nifty_close > 0 and net_change != 0:
                prev_close = nifty_close - net_change
                nifty_change_pct = (net_change / prev_close) * 100
        
        vix_quote = quotes.get(vix_key, {})
        if vix_quote:
            vix_close = float(vix_quote.get('last_price', 0))
    except Exception as e:
        logger.error(f"[Summary] Error fetching closing data: {e}")

    # Query today's trades from DB
    db = SessionLocal()
    try:
        query = db.query(Trade).filter(Trade.trade_date == today)
        if user_id:
            query = query.filter(Trade.user_id == user_id)
        trades = query.all()
    finally:
        db.close()

    total = len(trades)
    winners = sum(1 for t in trades if (t.realized_pnl or 0) > 0)
    losers = sum(1 for t in trades if (t.realized_pnl or 0) < 0)
    breakeven = total - winners - losers
    win_rate = (winners / total * 100) if total > 0 else 0

    total_pnl = sum(t.realized_pnl or 0 for t in trades)
    best_trade = max(trades, key=lambda t: t.realized_pnl or 0) if trades else None
    worst_trade = min(trades, key=lambda t: t.realized_pnl or 0) if trades else None

    best_str = f"{best_trade.symbol} +₹{best_trade.realized_pnl:.2f}" if best_trade and (best_trade.realized_pnl or 0) > 0 else "N/A"
    worst_str = f"{worst_trade.symbol} -₹{abs(worst_trade.realized_pnl or 0):.2f}" if worst_trade and (worst_trade.realized_pnl or 0) < 0 else "N/A"

    # ── No-trade day diagnostic ────────────────────────────────────────────────
    no_trade_note = ""
    l3_signals = stats.get('layer3_signals', 0)
    claude_enters = stats.get('claude_enters', 0)
    stocks_scanned = stats.get('stocks_scanned', 0)
    orb_signals = stats.get('orb_signals', 0)

    if total == 0:
        if l3_signals == 0 and (orb_signals or 0) == 0:
            no_trade_note = "\n\n💤 <b>No trades today</b> — No signals passed Layer 3 (market too flat / low-volume)."
        elif l3_signals > 0 and claude_enters == 0:
            no_trade_note = (
                f"\n\n💤 <b>No trades today</b> — {l3_signals} L3 signal(s) generated but all "
                f"rejected by Layer 4 risk filters (SL wider than 1.5x ATR for current market conditions)."
            )
        elif claude_enters > 0:
            no_trade_note = (
                f"\n\n💤 <b>No trades today</b> — Claude approved {claude_enters} signal(s) "
                f"but order execution did not complete (check execution logs)."
            )
        else:
            no_trade_note = "\n\n💤 <b>No trades today</b>."

    # ── Signal funnel ──────────────────────────────────────────────────────────
    layer4_passed = stats.get('layer4_passed', 0)
    claude_skips = stats.get('claude_skips_overriding_rules', 0)
    funnel = (
        f"  Watchlist size: {stocks_scanned}\n"
        f"  L3 momentum signals: {l3_signals}\n"
        f"  ORB breakout signals: {orb_signals}\n"
        f"  Passed Layer 4 (risk OK): {layer4_passed if layer4_passed else 'N/A'}\n"
        f"  Claude ENTER decisions: {claude_enters}\n"
        f"  Claude API calls: {stats.get('claude_calls', 'N/A')}\n"
        f"  Claude overrides: {claude_skips}"
    )

    summary = (
        f"📊 <b>DAILY SUMMARY — {date_str}</b>{no_trade_note}\n\n"
        f"<b>Trades:</b> {total} / {Config.MAX_TRADES_PER_DAY}\n"
        f"Winners: {winners} | Losers: {losers} | Breakeven: {breakeven}\n"
        f"Win rate: {win_rate:.0f}%\n\n"
        f"<b>P&L: ₹{total_pnl:+.2f}</b>\n"
        f"Best: {best_str}\n"
        f"Worst: {worst_str}\n\n"
        f"<b>Signal funnel:</b>\n{funnel}\n\n"
        f"<b>Market close:</b>\n"
        f"  Nifty: {nifty_close:.2f} ({nifty_change_pct:+.2f}% today)\n"
        f"  India VIX: {vix_close:.1f}"
    )

    alert_daily_summary(summary)
    logger.info(f"[Summary] Daily summary sent for {date_str}")
    return summary
