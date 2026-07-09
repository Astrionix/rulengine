import logging
import datetime
import uuid
from config import Config
from database import SessionLocal, Trade, DailyStats, SystemLog, get_ist_now, get_ist_date
import pytz

IST = pytz.timezone('Asia/Kolkata')

# Dedicated logger for execution storage
trade_logger = logging.getLogger('trades_log')
trade_logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('trades.log')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
if not trade_logger.handlers:
    trade_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)

class TradeManager:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.active_trades = []
        self.trade_history = []
        self._load_from_db()
        
    def _load_from_db(self):
        db = SessionLocal()
        try:
            today = get_ist_date()
            # Load OPEN trades for THIS user
            open_db_trades = db.query(Trade).filter(Trade.status == 'OPEN', Trade.user_id == self.user_id).all()
            for t in open_db_trades:
                try:
                    if t.trade_date and t.trade_date < today:
                        logger.warning(f"DB Recovery: Found stale OPEN trade {t.id} from {t.trade_date}. Auto-closing it.")
                        t.status = 'CLOSED'
                        t.exit_reason = 'STALE_EOD_CLEANUP'
                        t.exit_time = get_ist_now()
                        t.exit_price = t.entry_price
                        t.realized_pnl = 0.0
                        db.commit()
                        continue

                    # Recover if breakeven was triggered
                    is_buy = t.side.upper() == 'BUY'
                    breakeven_triggered = False
                    
                    sl = t.sl_price if t.sl_price is not None else 0.0
                    entry = t.entry_price if t.entry_price is not None else 0.0

                    if is_buy and sl >= entry:
                        breakeven_triggered = True
                    elif not is_buy and sl <= entry:
                        breakeven_triggered = True

                    self.active_trades.append({
                        'id': t.id,
                        'timestamp': t.entry_time.isoformat() if t.entry_time else get_ist_now().isoformat(),
                        'symbol': t.symbol,
                        'side': t.side,
                        'entry_price': t.entry_price,
                        'current_price': t.entry_price, # placeholder until LTP fetch
                        'max_favorable_price': t.entry_price,
                        'qty': t.quantity,
                        'sl': t.sl_price,
                        'initial_sl': t.sl_price,
                        'target': t.target_price,
                        'probability': 0.0,
                        'score': t.ai_score or 0.0,
                        'pnl': 0.0,
                        'status': 'OPEN',
                        'metadata': {},
                        'breakeven_triggered': breakeven_triggered,
                        'sl_order_id': None
                    })
                except Exception as loop_ex:
                    logger.error(f"Failed to recover specific trade {t.id} from DB: {loop_ex}")
                    continue
            
            # Load Today's CLOSED trades for THIS user
            closed_db_trades = db.query(Trade).filter(Trade.status == 'CLOSED', Trade.trade_date == today, Trade.user_id == self.user_id).all()
            for t in closed_db_trades:
                self.trade_history.append({
                    'id': t.id,
                    'timestamp': t.entry_time.isoformat() if t.entry_time else get_ist_now().isoformat(),
                    'symbol': t.symbol,
                    'side': t.side,
                    'entry_price': t.entry_price,
                    'current_price': t.exit_price or 0.0,
                    'qty': t.quantity,
                    'sl': t.sl_price,
                    'initial_sl': t.sl_price,
                    'probability': 0.0,
                    'score': t.ai_score or 0.0,
                    'pnl': t.realized_pnl or 0.0,
                    'status': 'CLOSED',
                    'exit_reason': t.exit_reason,
                    'exit_time': t.exit_time.isoformat() if t.exit_time else None,
                    'sl_order_id': None
                })

            # Calculate consecutive losses from today's closed trades (Bug Fix 3 recovery)
            self.consecutive_losses = 0
            sorted_trades = sorted(closed_db_trades, key=lambda t: t.exit_time or datetime.datetime.min)
            for t in sorted_trades:
                if (t.realized_pnl or 0) < 0:
                    self.consecutive_losses += 1
                elif (t.realized_pnl or 0) > 0:
                    self.consecutive_losses = 0

            # Update bot state if bot registered
            from user_bot_registry import get_bot
            bot = get_bot(self.user_id)
            if bot:
                bot.state.consecutive_losses = self.consecutive_losses

            logger.info(f"DB Recovery: Loaded {len(self.active_trades)} OPEN and {len(self.trade_history)} CLOSED trades for today. Consecutive losses: {self.consecutive_losses}")
        except Exception as e:
            logger.error(f"Failed to load from DB: {e}")
        finally:
            db.close()
        
    def add_trade(self, symbol: str, side: str, entry_price: float, qty: int, sl: float, target: float, probability: float, score: int, sl_order_id: str = None, metadata: dict = None):
        trade_id = str(uuid.uuid4())[:8]  # Shorter UUID for easy reading
        trade = {
            'id': trade_id,
            'timestamp': get_ist_now().isoformat(),
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'current_price': entry_price, # initial
            'max_favorable_price': entry_price, # Added for Peak Trailing
            'qty': int(qty),
            'sl': sl,
            'initial_sl': sl,
            'target': target,
            'probability': probability,
            'score': score,
            'pnl': 0.0,
            'status': 'OPEN',
            'metadata': metadata or {},
            'sl_order_id': sl_order_id
        }
        self.active_trades.append(trade)
        logger.info(f"Trade added: {trade}")
        trade_logger.info(f"EXECUTED NEW TRADE: [symbol={symbol}, side={side}, qty={qty}, entry={entry_price}, sl={sl}, target={target}, sl_order_id={sl_order_id}]")
        
        # Insert into Database
        db = SessionLocal()
        try:
            db_trade = Trade(
                id=trade_id,
                user_id=self.user_id,
                trade_date=get_ist_date(),
                symbol=symbol,
                side=side,
                quantity=qty,
                entry_price=entry_price,
                sl_price=sl,
                target_price=target,
                entry_time=get_ist_now(),
                status='OPEN',
                ai_score=float(score)
            )
            db.add(db_trade)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to insert trade into DB: {e}")
        finally:
            db.close()
        
    def update_prices(self, current_data: dict, execution_engine=None):
        '''
        current_data is a dict of symbol: current_price
        '''
        for trade in list(self.active_trades):
            sym = trade['symbol']
            if sym in current_data:
                price = current_data[sym]
                trade['current_price'] = price
                
                # Calculate PnL based on side
                if trade['side'] == 'BUY':
                    trade['pnl'] = (price - trade['entry_price']) * trade['qty']
                elif trade['side'] == 'SELL':
                    trade['pnl'] = (trade['entry_price'] - price) * trade['qty']
                
                self.check_exit_conditions(trade, execution_engine)
                self.manage_trailing_sl(trade, execution_engine)

    def check_exit_conditions(self, trade: dict, execution_engine=None):
        price = trade['current_price']
        sl = trade['sl']
        target = trade.get('target', 0.0)
        
        exit_trade = False
        exit_reason = ""
        
        if trade['side'] == 'BUY':
            if price <= sl:
                exit_trade = True
                exit_reason = "SL Hit"
        elif trade['side'] == 'SELL':
            if price >= sl:
                exit_trade = True
                exit_reason = "SL Hit"
                
        # Also check time > 3:15 PM
        now = get_ist_now()
        if now.hour == 15 and now.minute >= 15:
            exit_trade = True
            exit_reason = "Time Exit (> 3:15 PM)"

        if exit_trade:
            self.close_trade(trade, exit_reason, execution_engine)

    def manage_trailing_sl(self, trade: dict, execution_engine=None):
        """
        2-stage trailing Stop Loss logic (Section 5).
        1. When price reaches +1x ATR, trigger breakeven: Move SL to entry_price (one-time).
        2. When price reaches +2x ATR, trigger active trailing: Trail SL to (LTP - 1x ATR), updated every cycle.
        """
        import market_feed
        # Prevent trailing if tick feed is stale
        if not market_feed.is_tick_fresh(trade['symbol'], max_age=30.0):
            return

        entry = trade['entry_price']
        qty = trade['qty']
        side = trade['side']
        price = trade['current_price']
        sl_order_id = trade.get('sl_order_id')
        metadata = trade.get('metadata', {})
        
        # Use initial ATR saved into trade on entry
        atr_14 = metadata.get('atr_14')
        if not atr_14:
            atr_pct = metadata.get('atr_pct', 0.5) / 100
            atr_14 = entry * atr_pct if entry > 0 else 10.0
            
        if atr_14 <= 0:
            return

        if 'breakeven_triggered' not in trade:
            trade['breakeven_triggered'] = False

        new_sl = trade['sl']
        
        # 1. 2-stage trailing calculation
        if side == 'BUY':
            # Stage 1: Breakeven (+1x ATR)
            if not trade['breakeven_triggered'] and price >= entry + atr_14:
                new_sl = entry - (0.3 * atr_14)
                trade['breakeven_triggered'] = True
                logger.info(f"Trailing SL Stage 1: Breakeven triggered for {trade['symbol']} @ entry price {entry} with buffer {new_sl}")
            
            # Stage 2: Active Trailing (+2x ATR)
            if price >= entry + 2 * atr_14:
                candidate_sl = price - atr_14
                # Only move SL up
                if candidate_sl > new_sl:
                    new_sl = candidate_sl
                    logger.info(f"Trailing SL Stage 2: Active trailing for {trade['symbol']} @ {new_sl}")
        else: # SELL
            # Stage 1: Breakeven (+1x ATR)
            if not trade['breakeven_triggered'] and price <= entry - atr_14:
                new_sl = entry + (0.3 * atr_14)
                trade['breakeven_triggered'] = True
                logger.info(f"Trailing SL Stage 1: Breakeven triggered for {trade['symbol']} @ entry price {entry} with buffer {new_sl}")
            
            # Stage 2: Active Trailing (+2x ATR)
            if price <= entry - 2 * atr_14:
                candidate_sl = price + atr_14
                # Only move SL down
                if candidate_sl < new_sl:
                    new_sl = candidate_sl
                    logger.info(f"Trailing SL Stage 2: Active trailing for {trade['symbol']} @ {new_sl}")

        # Rounded to tick size (0.05)
        rounded_new_sl = round(new_sl * 20) / 20.0
        rounded_current_sl = round(trade['sl'] * 20) / 20.0

        # If a physically new SL triggers, process it on the server
        if abs(rounded_new_sl - rounded_current_sl) >= 0.05:
            if execution_engine:
                symbol_key = trade.get('instrument_token') or Config.SYMBOLS_MAPPING.get(trade['symbol'])
                
                # 1. Cancel existing SL order
                if sl_order_id:
                    cancel_res = execution_engine.cancel_order(sl_order_id)
                    logger.info(f"Canceled old SL order {sl_order_id} for {trade['symbol']}: {cancel_res}")
                
                # 2. Place new SL order
                place_res = execution_engine.place_stop_loss(symbol_key, side, qty, rounded_new_sl)
                
                # For virtual trades, we expect this to be rejected. We just update it internally anyway.
                if place_res.get('status') == 'success' or str(sl_order_id).startswith("virtual_"):
                    new_sl_order_id = place_res.get('order_id')
                    if not new_sl_order_id and str(sl_order_id).startswith("virtual_"):
                        new_sl_order_id = f"virtual_sl_{trade['symbol']}_{str(uuid.uuid4())[:6]}"
                        
                    trade['sl_order_id'] = new_sl_order_id
                    trade['sl'] = rounded_new_sl
                    
                    # Update DB
                    db = SessionLocal()
                    try:
                        db_trade = db.query(Trade).filter(Trade.id == trade['id']).first()
                        if db_trade:
                            db_trade.sl_price = rounded_new_sl
                            db_trade.sl_order_id = new_sl_order_id
                            db.commit()
                    except Exception as e:
                        logger.error(f"Failed to update SL in DB: {e}")
                    finally:
                        db.close()
                        
                    logger.info(f"Trailing SL updated successfully on server for {trade['symbol']} to {rounded_new_sl}")
                    
                    # Dispatch alert
                    try:
                        from telegram_alert import alert_trailing_sl_updated
                        stage = "BREAKEVEN" if rounded_new_sl == entry else "TRAILING"
                        alert_trailing_sl_updated(trade['symbol'], stage, rounded_current_sl, rounded_new_sl, price)
                    except Exception as ta_err:
                        logger.error(f"Failed to send trailing SL alert: {ta_err}")
                else:
                    logger.error(f"Failed to place new trailed SL for {trade['symbol']}")
            else:
                trade_logger.info(f"TRAILED LOCAL SL (2-Stage): {trade['symbol']} | Old: {rounded_current_sl} | New: {rounded_new_sl}")
                trade['sl'] = rounded_new_sl
                # Dispatch local trailing alert
                try:
                    from telegram_alert import alert_trailing_sl_updated
                    stage = "BREAKEVEN" if rounded_new_sl == entry else "TRAILING"
                    alert_trailing_sl_updated(trade['symbol'], stage, rounded_current_sl, rounded_new_sl, price)
                except Exception as ta_err:
                    logger.error(f"Failed to send trailing SL alert: {ta_err}")

    def close_trade(self, trade: dict, reason: str, execution_engine=None):
        trade['status'] = 'CLOSED'
        trade['exit_reason'] = reason
        trade['exit_time'] = get_ist_now().isoformat()
        
        sl_order_id = trade.get('sl_order_id')
        if execution_engine:
            # 1. Cancel pending SL order on Upstox
            if sl_order_id:
                try:
                    logger.info(f"Canceling pending SL order {sl_order_id} for {trade['symbol']} due to trade close ({reason})")
                    execution_engine.cancel_order(sl_order_id)
                except Exception as e:
                    logger.error(f"Failed to cancel SL order {sl_order_id}: {e}")
                    
            # 2. Place actual market exit order so it logs in Upstox (even if rejected)
            symbol_key = trade.get('instrument_token') or Config.SYMBOLS_MAPPING.get(trade['symbol'])
            if symbol_key:
                counter_side = "SELL" if trade['side'] == "BUY" else "BUY"
                logger.info(f"Submitting square-off order to Upstox for {trade['symbol']} ({reason})")
                try:
                    execution_engine.place_order_with_retry(symbol_key, counter_side, trade['qty'])
                except Exception as e:
                    logger.error(f"Failed to place exit order on broker: {e}")
        
        # Consecutive Losses Tracking (Now isolated by TradeManager instance)
        if not hasattr(self, 'consecutive_losses'):
            self.consecutive_losses = 0
            
        if trade['pnl'] < 0:
            self.consecutive_losses += 1
            logger.info(f"[USER {self.user_id}] PnL negative. Consecutive losses count: {self.consecutive_losses}")
        elif trade['pnl'] > 0:
            self.consecutive_losses = 0
            logger.info(f"[USER {self.user_id}] PnL positive. Consecutive losses reset to 0.")

        # Update bot state if bot registered
        from user_bot_registry import get_bot
        bot = get_bot(self.user_id)
        if bot:
            bot.state.consecutive_losses = self.consecutive_losses
            
        # Move lists
        self.trade_history.append(trade)
        if trade in self.active_trades:
            self.active_trades.remove(trade)

        # If consecutive losses reach 2, pause trading
        if self.consecutive_losses >= Config.MAX_CONSECUTIVE_LOSSES:
            from telegram_alert import alert_consecutive_losses
            if bot:
                bot.state.is_running = False
                bot.state.status = "STOPPED"
                bot.state.trading_halted = True
                logger.warning(f"[USER {self.user_id}] Pausing bot due to {self.consecutive_losses} consecutive losses.")
            alert_consecutive_losses(self.user_id)

        # Session P&L Circuit Breaker
        session_pnl = self.get_daily_pnl()
        active_capital = getattr(bot.state, 'active_capital', Config.MANUAL_CAPITAL) if bot else Config.MANUAL_CAPITAL
        daily_drawdown_limit = -(active_capital * 0.5 / 100) # 0.5% limit
        if session_pnl <= daily_drawdown_limit:
            if bot and not getattr(bot.state, 'trading_halted', False):
                bot.state.is_running = False
                bot.state.status = "STOPPED"
                bot.state.trading_halted = True
                logger.warning(f"[USER {self.user_id}] Session P&L {session_pnl} hit drawdown limit {daily_drawdown_limit}. Halting.")
                from telegram_alert import alert_telegram
                alert_telegram(f"🚨 Daily drawdown limit hit (P&L: {session_pnl:.2f}) — trading paused for the day.")
            
        logger.info(f"Trade closed: {trade['id']}, Reason: {reason}, PnL: {trade['pnl']}, Consecutive Losses: {self.consecutive_losses}")
        trade_logger.info(f"CLOSED TRADE: [id={trade['id']}, symbol={trade['symbol']}, reason={reason}, final_pnl={trade['pnl']}, exit_price={trade['current_price']}]")

        # Telegram Alert for Exit
        try:
            from telegram_alert import alert_trade_exit
            alert_trade_exit(trade['symbol'], reason, trade['entry_price'], trade['current_price'], trade['pnl'])
        except Exception as ta_err:
            logger.error(f"Failed to send trade exit alert: {ta_err}")

        # Update Database
        db = SessionLocal()
        try:
            db_trade = db.query(Trade).filter(Trade.id == trade['id'], Trade.user_id == self.user_id).first()
            if db_trade:
                db_trade.status = 'CLOSED'
                db_trade.exit_price = float(trade['current_price'])
                db_trade.exit_time = get_ist_now()
                db_trade.exit_reason = reason
                db_trade.realized_pnl = float(trade['pnl'])
                db_trade.sl_price = float(trade['sl'])
                db.commit()
                
            # Update Daily Stats aggregate for THIS user
            today = get_ist_date()
            stats = db.query(DailyStats).filter(DailyStats.session_date == today, DailyStats.user_id == self.user_id).first()
            if not stats:
                from database import UserConfig
                user_cfg = db.query(UserConfig).filter(UserConfig.user_id == self.user_id).first()
                initial_capital = user_cfg.manual_capital if user_cfg else Config.MANUAL_CAPITAL
                stats = DailyStats(
                    session_date=today, user_id=self.user_id,
                    start_capital=initial_capital, end_capital=initial_capital,
                    total_trades_taken=0, winning_trades=0, net_realized_pnl=0.0
                )
                db.add(stats)
            
            stats.total_trades_taken += 1
            if trade['pnl'] > 0:
                stats.winning_trades += 1
            stats.net_realized_pnl += float(trade['pnl'])
            stats.end_capital += float(trade['pnl'])
            db.commit()
        except Exception as e:
            logger.error(f"Failed to update trade exit in DB: {e}")
        finally:
            db.close()

    def get_daily_pnl(self) -> float:
        history_pnl = sum([t['pnl'] for t in self.trade_history if t['timestamp'][:10] == get_ist_now().isoformat()[:10]])
        active_pnl = sum([t['pnl'] for t in self.active_trades])
        return history_pnl + active_pnl

    def get_trades_taken_today(self) -> int:
        count = 0
        today_str = get_ist_now().isoformat()[:10]
        for t in self.trade_history + self.active_trades:
            if t['timestamp'][:10] == today_str:
                count += 1
        return count
