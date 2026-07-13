"""
pipeline_layer2.py — Watchlist Refresh (Section 3, Layer 2)
───────────────────────────────────────────────────────────
Runs at 10:00, 11:00, 12:00, 1:00, and 2:00 PM.
Drops dead stocks, adds emerging ones from the morning watchlist.

Output: Updated active_watchlist in memory AND persisted to database.
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime
import pytz

from config import Config
from database import (
    SessionLocal, MorningWatchlist, ActiveWatchlist, 
    get_ist_now, get_ist_date, get_daily_blacklist
)

IST = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)


class WatchlistRefresh:
    """Layer 2: Keep the watchlist fresh throughout the trading session."""

    def __init__(self, execution_engine, user_id: int = None):
        self.engine = execution_engine
        self.user_id = user_id
        self.active_watchlist = []  # List of dicts: {symbol, instrument_key, direction_bias}

    def load_active_watchlist(self) -> list:
        """Load active watchlist from DB (for startup recovery)."""
        db = SessionLocal()
        try:
            today = get_ist_date()
            rows = db.query(ActiveWatchlist).filter(
                ActiveWatchlist.date == today,
                ActiveWatchlist.removed_at.is_(None)
            ).all()
            self.active_watchlist = [{
                "symbol": r.symbol,
                "instrument_key": r.instrument_key,
                "direction_bias": r.direction_bias,
            } for r in rows]
            logger.info(f"[Layer 2] Loaded {len(self.active_watchlist)} active stocks from DB.")
            return self.active_watchlist
        finally:
            db.close()

    def refresh(self) -> list:
        """Execute Layer 2 watchlist refresh. Returns updated active watchlist."""
        logger.info(f"[Layer 2] Starting watchlist refresh at {get_ist_now().strftime('%H:%M')}...")
        
        # Get current active symbols
        if not self.active_watchlist:
            self.load_active_watchlist()

        dropped = self._drop_dead_stocks()
        added = self._add_emerging_stocks()

        logger.info(f"[Layer 2] Refresh complete. Dropped: {len(dropped)}, Added: {len(added)}. "
                     f"Active watchlist: {len(self.active_watchlist)} stocks.")
        return self.active_watchlist

    def _drop_dead_stocks(self) -> list:
        """Drop stocks meeting ALL three dead-stock conditions."""
        dropped = []
        remaining = []

        for stock in self.active_watchlist:
            try:
                should_drop = self._check_drop_conditions(stock)
                if should_drop:
                    dropped.append(stock)
                    self._mark_removed_in_db(stock["symbol"], "Dead stock: sideways RSI + low volume + flat slope")
                else:
                    remaining.append(stock)
            except Exception as e:
                logger.error(f"[Layer 2] Error evaluating drop for {stock['symbol']}: {e}")
                remaining.append(stock)  # Keep on error

        self.active_watchlist = remaining
        if dropped:
            logger.info(f"[Layer 2] Dropped {len(dropped)} dead stocks: {[s['symbol'] for s in dropped]}")
        return dropped

    def _check_drop_conditions(self, stock: dict) -> bool:
        """Check if ALL three drop conditions are met. Returns True if stock should be dropped."""
        df = self.engine.get_ohlc(stock["instrument_key"], interval="5minute")
        if df.empty or len(df) < 20:
            return False

        # Condition 1: RSI(14) between 44 and 56 (sideways)
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        rsi = (100 - (100 / (1 + rs))).iloc[-1]
        
        if not (44 <= rsi <= 56):
            return False  # Not sideways — keep

        # Condition 2: Volume in last 3 candles below 20-bar average
        vol_ma_20 = df['volume'].iloc[-21:-1].mean() if len(df) >= 21 else df['volume'].mean()
        last_3_avg_vol = df['volume'].iloc[-3:].mean()
        
        if last_3_avg_vol >= vol_ma_20:
            return False  # Volume is fine — keep

        # Condition 3: Price slope over last 10 candles is flat
        if len(df) >= 10:
            prices = df['close'].iloc[-10:].values
            x = np.arange(len(prices))
            slope = np.polyfit(x, prices, 1)[0]
            normalized_slope = (slope / prices[-1]) * 100  # as % per candle
            
            if abs(normalized_slope) > 0.05:  # Not flat
                return False

        # All three conditions met — drop
        return True

    def _add_emerging_stocks(self) -> list:
        """Add stocks from morning watchlist that show sudden activity."""
        added = []
        active_symbols = {s["symbol"] for s in self.active_watchlist}
        blacklist = set(get_daily_blacklist(self.user_id)) if self.user_id else set()

        # Get all morning watchlist stocks not already active
        db = SessionLocal()
        try:
            today = get_ist_date()
            morning_stocks = db.query(MorningWatchlist).filter(
                MorningWatchlist.date == today
            ).all()
        finally:
            db.close()

        for ms in morning_stocks:
            if ms.symbol in active_symbols:
                continue  # Already in active watchlist
            if ms.symbol in blacklist:
                continue  # Already traded today

            try:
                # Check volume surge condition
                df = self.engine.get_ohlc(ms.instrument_key, interval="5minute")
                if df.empty or len(df) < 20:
                    continue

                vol_ma_20 = df['volume'].iloc[-21:-1].mean() if len(df) >= 21 else df['volume'].mean()
                last_3_avg_vol = df['volume'].iloc[-3:].mean()

                # Volume in last 3 candles > 2x the 20-bar average (sudden activity)
                if vol_ma_20 > 0 and last_3_avg_vol >= 2.0 * vol_ma_20:
                    new_stock = {
                        "symbol": ms.symbol,
                        "instrument_key": ms.instrument_key,
                        "direction_bias": ms.direction_bias,
                        "discovery_score": ms.discovery_score,
                        "discovery_rank": ms.discovery_rank
                    }
                    self.active_watchlist.append(new_stock)
                    added.append(new_stock)
                    self._add_to_active_db(ms.symbol, ms.instrument_key, ms.direction_bias)

            except Exception as e:
                logger.error(f"[Layer 2] Error checking {ms.symbol} for addition: {e}")
                continue

        # ── Gracefully fetch fresh live breakouts from NSE (fail-safe) ────────
        try:
            from watchlist_scraper import fetch_breakout_stocks
            from nifty500_instruments import get_universe
            scraped_stocks = fetch_breakout_stocks()
            universe = get_universe()
            scraped_added = 0
            
            # Re-fetch active_symbols in case any were added above
            active_symbols = {s["symbol"] for s in self.active_watchlist}
            
            for item in scraped_stocks:
                symbol = item["symbol"]
                if symbol in universe and symbol not in active_symbols and symbol not in blacklist:
                    new_stock = {
                        "symbol": symbol,
                        "instrument_key": universe[symbol],
                        "direction_bias": item["direction_bias"],
                    }
                    self.active_watchlist.append(new_stock)
                    self._add_to_active_db(symbol, universe[symbol], item["direction_bias"])
                    added.append(new_stock)
                    scraped_added += 1
                    active_symbols.add(symbol)
            if scraped_added > 0:
                logger.info(f"[Layer 2] Scraper successfully added {scraped_added} new breakout stocks to active watchlist.")
        except Exception as scraper_err:
            logger.error(f"[Layer 2] Hourly breakout scraping failed: {scraper_err}. Continuing with standard watchlist.")

        if added:
            logger.info(f"[Layer 2] Added {len(added)} emerging stocks: {[s['symbol'] for s in added]}")
        return added

    def _mark_removed_in_db(self, symbol: str, reason: str):
        """Mark a stock as removed from active watchlist in DB."""
        db = SessionLocal()
        try:
            today = get_ist_date()
            row = db.query(ActiveWatchlist).filter(
                ActiveWatchlist.date == today,
                ActiveWatchlist.symbol == symbol,
                ActiveWatchlist.removed_at.is_(None)
            ).first()
            if row:
                row.removed_at = get_ist_now()
                row.removal_reason = reason[:100]
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Layer 2] Error marking {symbol} as removed: {e}")
        finally:
            db.close()

    def _add_to_active_db(self, symbol: str, instrument_key: str, direction_bias: str):
        """Add a stock to active watchlist in DB."""
        db = SessionLocal()
        try:
            today = get_ist_date()
            # Check if already exists (re-add case)
            existing = db.query(ActiveWatchlist).filter(
                ActiveWatchlist.date == today,
                ActiveWatchlist.symbol == symbol,
            ).first()
            if existing:
                # Re-activate
                existing.removed_at = None
                existing.removal_reason = None
            else:
                entry = ActiveWatchlist(
                    date=today,
                    symbol=symbol,
                    instrument_key=instrument_key,
                    direction_bias=direction_bias,
                    added_at=get_ist_now(),
                )
                db.add(entry)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Layer 2] Error adding {symbol} to active watchlist: {e}")
        finally:
            db.close()
