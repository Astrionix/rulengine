"""
reconciliation.py — Daily Reconciliation (Section 10)
──────────────────────────────────────────────────────
Runs at 3:35 PM after market close.
Compares the bot's database records against Upstox's official trade book.
"""

import json
import logging
from datetime import datetime
import pytz

from config import Config
from database import SessionLocal, Trade, DailyReconciliation, get_ist_date, get_ist_now
from telegram_alert import alert_reconciliation

IST = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)


def run_reconciliation(execution_engine, user_id: int = None, intraday: bool = False) -> dict:
    """
    Reconcile DB trades against Upstox trade book.
    
    Args:
        execution_engine: For fetching the Upstox trade book
        user_id: Optional user_id filter
        intraday: If True, checks both OPEN and CLOSED trades and alerts only on mismatch without DB persistence.
        
    Returns:
        Dict with reconciliation results.
    """
    today = get_ist_date()
    logger.info(f"[Reconciliation] Starting (intraday={intraday}) for {today}...")

    # Fetch DB trades
    db = SessionLocal()
    try:
        query = db.query(Trade).filter(
            Trade.trade_date == today
        )
        if not intraday:
            query = query.filter(Trade.status == "CLOSED")
            
        if user_id:
            query = query.filter(Trade.user_id == user_id)
        db_trades = query.all()
    finally:
        db.close()

    # Fetch Upstox trade book
    upstox_trades = execution_engine.get_trade_book()

    # Build lookup by instrument key
    upstox_by_symbol = {}
    for ut in upstox_trades:
        key = ut.get('instrument_token', '')
        if key not in upstox_by_symbol:
            upstox_by_symbol[key] = []
        upstox_by_symbol[key].append(ut)

    mismatches = []
    ghost_trades = []
    matched_upstox_ids = set()

    # Compare each DB trade against Upstox book
    for db_trade in db_trades:
        symbol = db_trade.symbol
        instrument_key = Config.SYMBOLS_MAPPING.get(symbol, "")
        
        # Find matching Upstox trade
        matched = False
        upstox_list = upstox_by_symbol.get(instrument_key, [])
        
        for ut in upstox_list:
            ut_id = ut.get('trade_id', ut.get('order_id', ''))
            if ut_id in matched_upstox_ids:
                continue
            
            # Match by approximate entry price and quantity
            ut_price = float(ut.get('average_price', 0))
            ut_qty = int(ut.get('filled_quantity', ut.get('quantity', 0)))
            
            price_diff = abs(ut_price - (db_trade.entry_price or 0))
            qty_match = ut_qty == (db_trade.quantity or 0)
            
            if qty_match and price_diff < (db_trade.entry_price or 1) * 0.02:
                matched = True
                matched_upstox_ids.add(ut_id)
                
                # Check for mismatches in specific fields
                issues = []
                if price_diff > 0.5:
                    issues.append(f"entry_price: DB={db_trade.entry_price} vs Upstox={ut_price}")
                
                if issues:
                    mismatches.append({
                        "trade_id": db_trade.id,
                        "symbol": symbol,
                        "issues": issues,
                    })
                break
        
        if not matched:
            ghost_trades.append({
                "trade_id": db_trade.id,
                "symbol": symbol,
                "entry_price": db_trade.entry_price,
                "quantity": db_trade.quantity,
            })

    # Check for untracked trades (in Upstox but not in DB)
    untracked = []
    for instrument_key, ut_list in upstox_by_symbol.items():
        for ut in ut_list:
            ut_id = ut.get('trade_id', ut.get('order_id', ''))
            if ut_id not in matched_upstox_ids:
                tag = ut.get('tag', '')
                if tag and 'intBot' in tag:
                    untracked.append({
                        "trade_id": ut_id,
                        "instrument_key": instrument_key,
                        "price": ut.get('average_price'),
                        "quantity": ut.get('quantity'),
                    })

    # Save results to DB
    if not intraday:
        details_dict = {
            "mismatches": mismatches,
            "ghost_trades": ghost_trades,
            "untracked_trades": untracked,
        }
        details_json = json.dumps(details_dict, default=str)

        db = SessionLocal()
        try:
            recon = DailyReconciliation(
                date=today,
                total_trades=len(db_trades),
                mismatches=len(mismatches),
                ghost_trades=len(ghost_trades),
                untracked_trades=len(untracked),
                details=details_json,
            )
            db.add(recon)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Reconciliation] Error saving results: {e}")
        finally:
            db.close()

    # Send Telegram alert
    details_str = ""
    if mismatches:
        details_str += "MISMATCHES:\n" + "\n".join(
            f"  {m['symbol']} ({m['trade_id']}): {', '.join(m['issues'])}" for m in mismatches
        ) + "\n"
    if ghost_trades:
        details_str += "GHOST TRADES (in DB but not in Upstox):\n" + "\n".join(
            f"  {g['symbol']} ({g['trade_id']}): ₹{g['entry_price']} x{g['quantity']}" for g in ghost_trades
        ) + "\n"
    if untracked:
        details_str += "UNTRACKED (in Upstox but not in DB):\n" + "\n".join(
            f"  {u['instrument_key']} ({u['trade_id']}): ₹{u['price']} x{u['quantity']}" for u in untracked
        )

    if intraday:
        if mismatches or ghost_trades or untracked:
            from telegram_alert import send_telegram_critical
            msg = (
                f"🚨 <b>INTRADAY RECONCILIATION DISCREPANCY DETECTED</b>\n"
                f"• Mismatches: {len(mismatches)}\n"
                f"• Ghost trades (in DB only): {len(ghost_trades)}\n"
                f"• Untracked trades (in Upstox only): {len(untracked)}\n\n"
                f"📋 Details:\n{details_str[:3000]}"
            )
            send_telegram_critical(msg)
    else:
        alert_reconciliation(len(mismatches), len(ghost_trades), len(untracked), details_str)

    result = {
        "total_trades": len(db_trades),
        "mismatches": len(mismatches),
        "ghost_trades": len(ghost_trades),
        "untracked_trades": len(untracked),
    }
    logger.info(f"[Reconciliation] Complete: {result}")
    return result
