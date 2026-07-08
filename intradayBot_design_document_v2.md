# intradayBot — Complete System Design Document (v2)
## For Antigravity to Implement

---

## 1. Project Context

This is an existing Python intraday trading bot (`intradayBot`) that connects to the Upstox API.
It already has: WebSocket market feed, order execution engine, trade manager, MySQL database, and a FastAPI dashboard.

**Tech Stack:** Python, FastAPI, MySQL, Upstox API, Claude API (Anthropic, model: `claude-sonnet-4-6`)

**What this document covers:**
- Fix 7 critical bugs in the existing codebase
- Replace `strategy.py` and `ai_filter.py` with a new continuous scanning pipeline
- Add watchlist refresh, Telegram alerting, and global kill switch

**What must NOT be changed:**
- `credential_service.py` — AES-256-GCM encryption is correct, keep as-is
- `crypto_utils.py` — keep as-is
- `auth.py` — JWT authentication is correct, keep as-is
- `auth_middleware.py` — keep as-is
- `market_feed.py` — keep WebSocket logic, but add timestamp to tick cache (see Bug 4)
- FastAPI dashboard UI — keep all existing UI, only add new elements described in Section 12
- Multi-tenant UserBot task structure — keep per-user isolation

---

## 2. Critical Bug Fixes (Implement First, Before Anything Else)

---

### Bug 1 — Ghost Position on Order Timeout
**File:** `execution.py`

**Problem:** If an entry order times out but actually succeeded on Upstox's side, the bot assumes failure, skips placing the stop-loss, and leaves an untracked open position with no protection.

**Fix logic:**
After any order placement that times out or returns a network error, wait 2 seconds, then call the Upstox order status API using the order ID to verify the actual server-side status. If the order succeeded on Upstox's side, treat it as a successful entry and proceed with stop-loss placement normally. If the status check also fails, do not assume the position is open or closed — log the order ID with a CRITICAL flag, send a Telegram alert saying "ORDER STATUS UNKNOWN — manual check required for [symbol]", and do not place any further orders for that symbol for the rest of the day.

---

### Bug 2 — Partial Fill Creates Reverse Position
**File:** `execution.py`

**Problem:** The bot submits stop-loss for the full requested quantity even when only a partial fill occurred. When the SL triggers for the full quantity, it creates an unintended reverse position for the unfilled portion.

**Fix logic:**
After order confirmation, read the actual `filled_quantity` from the Upstox order response object — not the originally requested quantity. Use `filled_quantity` for all subsequent calculations: SL order quantity, position sizing, and P&L tracking. If `filled_quantity` is zero, treat the entire order as failed and do not place any SL order.

---

### Bug 3 — SL Order ID Lost on Server Restart
**File:** `trade_manager.py` and `database.py`

**Problem:** `sl_order_id` is stored only in memory. On server restart, it resets to `None`. The recovered bot cannot trail or cancel the existing SL order on Upstox, leaving it as an orphan.

**Fix logic:**
Add a column `sl_order_id VARCHAR(64)` to the `trades` table in the database if it does not already exist. Every time an SL order is successfully placed or updated (during trailing), immediately write its `sl_order_id` to this column in the database. On server restart, when recovering active trades from the database, read `sl_order_id` from the database and restore it into the TradeManager instance in memory. The bot can then trail and cancel the SL order normally.

---

### Bug 4 — Stale Market Feed Used for Trade Decisions
**File:** `market_feed.py` and `main.py`

**Problem:** The tick cache memory dictionary is never cleared. If the WebSocket disconnects, stale prices remain cached. The execution loop reads these stale prices without checking freshness and can place trades based on data that is many minutes old.

**Fix logic:**
Modify the tick cache so every price update stores a timestamp alongside the price, structured as `{"price": 924.5, "updated_at": datetime.now()}`. Before any signal evaluation, order placement, or trailing stop modification, check that `updated_at` for that symbol is within the last 30 seconds. If the timestamp is older than 30 seconds, skip that action entirely for that symbol in that cycle and log a warning: "Stale feed for [symbol] — skipping." Do not trade on stale data under any circumstance.

---

### Bug 5 — MAX_TRADES_PER_DAY Not Enforced
**File:** `main.py`

**Problem:** `MAX_TRADES_PER_DAY = 5` exists in config but no enforcement logic is present. The bot continues taking new trades even after the daily limit is exceeded.

**Fix logic:**
At the start of each signal evaluation cycle for each user, query the database to count completed and active trades for that user for today's date. If the count is greater than or equal to `MAX_TRADES_PER_DAY`, skip signal evaluation entirely for the rest of the day for that user and log: "Daily trade limit reached for user [id]." This count must be checked every cycle, not just once at startup.

---

### Bug 6 — Timeframe Mismatch in AI Filter Prompt
**File:** `ai_filter.py` (being replaced) and any prompt strings

**Problem:** Strategy fetches 5-minute candles but the AI filter prompt tells the LLM it is analyzing 1-minute charts. All thresholds and signal interpretation are miscalibrated as a result.

**Fix logic:**
Standardize the entire system on 5-minute candles. Every prompt string, comment, log message, and config value that references "1-minute" or "1min" must be updated to "5-minute" or "5min." This must be consistent across all files.

---

### Bug 7 — No Global Kill Switch
**File:** `main.py`, `database.py`, FastAPI dashboard

**Problem:** A per-user emergency stop exists but there is no way to halt all users simultaneously in a system-wide emergency.

**Fix logic:**
Add a row to a `system_config` database table with key `GLOBAL_TRADING_ENABLED` and default value `true`. At the very start of each user bot loop iteration, before doing anything else, check this flag. If the value is `false`, skip signal evaluation and order placement for all users immediately.

**Critical distinction — what continues during a global halt:**
- Active position monitoring (Section 5) MUST continue running normally even when the global halt is active. Existing open positions must still be monitored for SL hits, target hits, trailing stops, and the 3:15 PM force exit. The halt only stops new trade entries, not protection of existing ones.
- The monitoring loop in Section 5 must NOT check the global halt flag. Only the signal evaluation and order entry path checks this flag.

Add two admin API endpoints: `POST /api/admin/halt` sets the flag to `false`, and `POST /api/admin/resume` sets it to `true`. The FastAPI dashboard must display the current global status prominently at the top of the page with a visible red banner when halted.

---

## 3. Replace strategy.py — New Continuous Scanning Pipeline

The existing `strategy.py` and `ai_filter.py` are fully replaced by a new 5-layer pipeline described below. Delete the old files after implementing the new pipeline.

---

### Overview of the New Pipeline

```
9:00 AM  — Layer 1: Morning pre-filter on Nifty 500 (runs once)
9:15 AM  — Market opens, WebSocket feed active
Every 60 min — Layer 2: Watchlist refresh (10:00 AM, 11:00 AM, 12:00 PM, 1:00 PM, 2:00 PM)
Every 5 min  — Layer 3: Continuous signal scanner on active watchlist
              Layer 4: Feature extraction for qualifying stocks (max 5 per cycle)
              Layer 5: Claude API batch call → ENTER or SKIP decision
              → Order placement for ENTER decisions
```

---

### Layer 1 — Morning Pre-Filter (Runs Once at 9:00 AM)

**Purpose:** Reduce 500 stocks to a focused watchlist of 80–120 stocks before the market opens.

**Data source:** Previous day's daily OHLCV candles for all Nifty 500 stocks, fetched at 9:00 AM via Upstox historical data API.

**A stock is added to the morning watchlist only if it passes ALL of the following:**

```
Rule 1 — Volume Activity:
  Yesterday's volume > 1.3x the 20-day average daily volume.
  Reason: Stock is attracting attention. Volume precedes price.

Rule 2 — Trend Filter:
  For LONG candidates: yesterday's closing price is ABOVE the 20-day EMA on the daily chart.
  For SHORT candidates: yesterday's closing price is BELOW the 20-day EMA on the daily chart.
  Reason: Only trade in the direction of the prevailing daily trend.

Rule 3 — Not Extended (Breakout Zone Only):
  For LONG candidates: closing price is no more than 8% BELOW the 20-day rolling high.
    Meaning: the stock is within striking distance of its recent high — a potential breakout
    zone — but has NOT already surged far past it. If price is more than 8% below the
    20-day high, the stock is too far away from momentum. If price is already significantly
    above the 20-day high (e.g., 5%+ extended above it), that extended condition is caught
    by the ATR/proximity check in Layer 3.
  For SHORT candidates: closing price is no more than 8% ABOVE the 20-day rolling low.
    Meaning: the stock is near its recent low — a potential breakdown zone.
  Reason: Target stocks approaching key levels, not stocks already exhausted past them.

Rule 4 — Relative Strength:
  For LONG candidates: stock's 5-day return > Nifty 500 index 5-day return.
  For SHORT candidates: stock's 5-day return < Nifty 500 index 5-day return.
  Reason: Strong stocks stay strong. Weak stocks stay weak.
```

**Output:** 80–120 stocks saved to a `morning_watchlist` table in the database with timestamp and direction tag (LONG_CANDIDATE or SHORT_CANDIDATE). This list is the starting point for all intraday scanning.

**On startup recovery:** If the bot restarts after 9:00 AM, reload today's morning_watchlist from the database. Do not re-fetch from Upstox.

---

### Layer 2 — Watchlist Refresh (Runs at 10:00, 11:00, 12:00, 1:00, and 2:00 PM)

**Purpose:** Keep the watchlist fresh throughout the entire trading session. Drop dead stocks, add emerging ones. The 2:00 PM refresh is the final one — enough time for signals to form before the 2:30 PM cutoff.

**Drop a stock from the active watchlist if ALL three conditions are true:**
```
- RSI(14) on 5-min chart is between 44 and 56 (no momentum, going sideways)
- AND volume in the last 3 candles is below the 20-bar average volume
- AND price slope over last 10 candles is flat (linear regression slope near 0)
```
All three conditions must be true to drop. One or two conditions alone is not enough.

**Add a stock to the active watchlist if ALL three conditions are true:**
```
- It is in the morning_watchlist for today (must have passed Layer 1)
- AND volume in the last 3 candles is above 2x the 20-bar average (sudden activity)
- AND it is not already in the daily_blacklist for this user today (not traded already)
```

**Output:** Updated `active_watchlist` in memory AND persisted to database. Typically 40–80 stocks at any given time during the day.

**On startup recovery:** If the bot restarts mid-day, reload today's active_watchlist from the database (records where `removed_at` is NULL and `date` = today). Do not wait for the next hourly refresh — the bot must have a working watchlist immediately after restart.

---

### Layer 3 — Continuous Signal Scanner (Runs Every 5 Minutes, 9:20 AM to 2:30 PM)

**Purpose:** For each stock in the active watchlist, quickly check if it meets minimum signal conditions. This runs entirely in Python with no external API calls — it is fast and cheap.

**Before scanning, skip this cycle entirely if:**
```
- Global trading flag is false (system halted — new entries blocked)
- User has reached MAX_TRADES_PER_DAY
- Current time is after 2:30 PM
- India VIX is above 25 (extreme market fear — no new trades)
```

**For each stock, calculate these features from the 5-minute candle stream:**
```python
vwap             = session-anchored VWAP from 9:15 AM
rsi_14           = RSI with period 14 on 5-min candles
ema_20           = EMA with period 20 on 5-min candles
ema_20_prev      = EMA 20 of the previous candle
vol_ratio_6bar   = current candle volume / 6-bar average volume
candle_strength  = abs(close - open) / (high - low)  [0 to 1; skip if high == low]
price_vs_vwap    = (close - vwap) / vwap * 100        [percentage]
```

**Minimum threshold — stock passes Layer 3 only if 3 or more of these 5 conditions are met:**

For LONG signal:
```
Condition 1: price_vs_vwap > +0.15%          (price clearly above VWAP)
Condition 2: vol_ratio_6bar >= 2.0           (volume at least 2x recent average)
Condition 3: rsi_14 >= 58 AND rsi_14 <= 75  (bullish momentum, not yet overbought)
Condition 4: ema_20 > ema_20_prev            (EMA clearly rising)
Condition 5: candle_strength >= 0.50         (clean bullish candle body, not a doji)
```

For SHORT signal:
```
Condition 1: price_vs_vwap < -0.15%          (price clearly below VWAP)
Condition 2: vol_ratio_6bar >= 2.0           (volume at least 2x recent average)
Condition 3: rsi_14 <= 42 AND rsi_14 >= 25  (bearish momentum confirmed, not yet exhausted/oversold)
Condition 4: ema_20 < ema_20_prev            (EMA clearly falling)
Condition 5: candle_strength >= 0.50         (clean bearish candle body)
```

**Also skip a stock in Layer 3 if:**
```
- It is already in an active trade for this user
- It has been traded today by this user (daily blacklist)
- Its tick data is stale (last update older than 30 seconds)
```

**Output:** A list of stocks that passed Layer 3 in this 5-minute cycle, each tagged as LONG or SHORT candidate. If more than 5 stocks qualify in a single cycle, take the top 5 ranked by number of conditions met (descending), then by vol_ratio_6bar as a tiebreaker. This cap prevents API burst load in Layer 4.

---

### Layer 4 — Feature Extraction (Runs Immediately After Layer 3)

**Purpose:** For each stock that passed Layer 3 (maximum 5), extract a rich set of pre-computed features to send to Claude. Do not send raw candle arrays — only computed values.

**Candle data caching rule:**
Do NOT fetch 30 candles fresh from Upstox for every stock on every 5-minute cycle. Instead, maintain a rolling candle cache per symbol that is refreshed every 10 minutes (i.e., every 2 scan cycles). On each Layer 4 call, use the cached candle data. This prevents an API burst of 5 separate Upstox historical calls every 5 minutes.

**Fetch or use cached values for:**
- Last 30 candles on 5-min timeframe per qualifying stock (from rolling cache)
- Current Nifty 50 trend (up/down/sideways based on today's price vs yesterday's close) — refresh every 15 minutes
- India VIX current value — refresh every 15 minutes
- Sector performance vs Nifty today (percentage) — refresh every 30 minutes

**Feature payload per stock:**

```python
{
  "symbol": "TATAMOTORS",
  "signal_direction": "LONG",       # or "SHORT"
  "layer3_conditions_met": 4,       # how many of the 5 conditions passed (max 5)

  "price_context": {
    "ltp": 924.5,
    "price_vs_vwap_pct": 0.82,
    "price_vs_ema20_daily_pct": 1.2,
    "price_vs_20day_high_pct": -3.5,  # negative = price is below 20-day high (room to run for LONG)
    "atr_14_5min": 8.5,
    "atr_pct": 0.92                   # ATR as % of price
  },

  "momentum": {
    "rsi_14_5min": 63,
    "rsi_14_daily": 61,
    "ema20_5min_slope": "rising",     # "rising" / "falling" / "flat"
    "candle_strength_last": 0.72,
    "price_slope_10bar": 0.38
  },

  "volume": {
    "vol_ratio_20day_daily": 2.8,
    "vol_ratio_6bar_5min": 2.1,
    "volume_trend": "expanding"       # "expanding" / "contracting" / "flat"
  },

  "levels": {
    "support_20bar_5min": 912.0,
    "resistance_20bar_5min": 931.0,
    "distance_to_resistance_pct": 0.71,   # how far price is below resistance
    "distance_to_support_pct": 1.36       # how far price is above support
  },

  "market_context": {
    "nifty_trend_today": "bullish",   # "bullish" / "bearish" / "sideways"
    "india_vix": 13.4,
    "sector": "Auto",
    "sector_vs_nifty_today_pct": 0.6,
    "stock_vs_nifty_5day_pct": 2.3
  },

  "last_3_candles_5min": [
    {"o": 918, "h": 926, "l": 916, "c": 924, "v": 145000},
    {"o": 924, "h": 929, "l": 922, "c": 927, "v": 189000},
    {"o": 927, "h": 931, "l": 925, "c": 924, "v": 98000}
  ]
}
```

**Output:** A JSON array of feature payloads, one per qualifying stock. Maximum 5 stocks per cycle.

---

### Layer 5 — Claude API Analysis (Final Gate, Replaces ai_filter.py Entirely)

**Purpose:** Send all qualifying stocks from Layer 4 in a single batched API call to Claude. Claude returns a structured ENTER or SKIP decision for each.

**API configuration:**
```
Model: claude-sonnet-4-6
Max tokens: 2000
Temperature: 0.0  (deterministic, no randomness in decisions)
```

---

**System prompt (send this as the system message):**

```
You are a disciplined, risk-first intraday trading analyst for Indian equity markets (NSE).
All data you receive is from 5-minute candles. Your sole job is to filter a pre-screened
list of stocks and decide: ENTER this trade, or SKIP it.

The stocks you receive have already passed a quantitative signal screener (Layer 3). Your
role is NOT to re-run the screener — it is to apply judgment: catch setups that look good
on paper but have subtle flaws, confirm setups that are genuinely strong, and provide
precise, structure-based entry, stop-loss, and target prices.

─── OUTPUT FORMAT ───────────────────────────────────────────────────────────────
Return ONLY a valid JSON array. One object per stock, in the exact same order as input.
No text before or after the JSON. No markdown fences. No explanation outside the JSON.

Each object must contain exactly these fields:
{
  "symbol":        <string>  — the stock symbol exactly as provided,
  "decision":      <"ENTER" | "SKIP">,
  "direction":     <"LONG" | "SHORT">  — must always match signal_direction provided,
  "entry_price":   <float>   — precise entry price in rupees (2 decimal places),
  "stop_loss":     <float>   — precise stop-loss price in rupees (2 decimal places),
  "target":        <float>   — precise target price in rupees (2 decimal places),
  "confidence":    <integer 1–5>  — 5 = very strong, 3 = acceptable, 1–2 = too weak,
  "reasoning":     <string>  — exactly 2 sentences: sentence 1 explains why ENTER or SKIP,
                               sentence 2 names the single most important factor,
  "red_flags":     <string>  — specific concerns found in the data, or "none"
}

─── ENTRY PRICE RULES ───────────────────────────────────────────────────────────
- For LONG: set entry_price at LTP + (0.05% to 0.10% buffer above LTP). This accounts
  for MARKET order slippage. Do not set entry at a price below LTP for a LONG.
- For SHORT: set entry_price at LTP - (0.05% to 0.10% buffer below LTP).
- Never set entry_price more than 0.3% away from LTP in either direction.

─── STOP-LOSS RULES ──────────────────────────────────────────────────────────────
- Stop-loss must be placed at a logical market structure level, not at an arbitrary
  distance. For LONG: SL goes just below support_20bar_5min (subtract 0.1% buffer).
  For SHORT: SL goes just above resistance_20bar_5min (add 0.1% buffer).
- If support/resistance is too close (risk < 0.3% of entry price), widen SL to
  1x ATR below entry for LONG, 1x ATR above entry for SHORT.
- Minimum SL distance: 0.3% of entry price.
- Maximum SL distance: 1.5x ATR_14_5min from entry. If logical structure SL would be
  wider than 1.5x ATR, return SKIP — the risk is too large.

─── TARGET RULES ─────────────────────────────────────────────────────────────────
- Target must deliver a minimum 1.5:1 reward-to-risk ratio.
  risk = abs(entry_price - stop_loss)
  target must be at least: entry_price + (1.5 * risk) for LONG
                            entry_price - (1.5 * risk) for SHORT
- For LONG: target should not exceed resistance_20bar_5min unless distance_to_resistance
  is very small (< 0.5%) and volume/momentum strongly supports a breakout.
- Preferred target is at the next meaningful resistance level with R:R >= 1.5.

─── DECISION RULES ──────────────────────────────────────────────────────────────
Apply these rules strictly and in order. The first matching rule that requires SKIP
overrides everything else:

MANDATORY SKIP conditions (return SKIP immediately, confidence is irrelevant):
  1. signal_direction does not match the clear price action. If data suggests LONG but
     signal_direction is SHORT (or vice versa), return SKIP with red_flag explanation.
     Never change direction — always return the direction matching signal_direction.
  2. price_vs_vwap_pct is against the signal direction:
     LONG + price_vs_vwap_pct <= 0 → SKIP (price is at or below VWAP for a LONG setup)
     SHORT + price_vs_vwap_pct >= 0 → SKIP (price is at or above VWAP for a SHORT setup)
  3. For LONG: distance_to_resistance_pct < 0.3% → SKIP (stock is against resistance)
     For SHORT: distance_to_support_pct < 0.3% → SKIP (stock is against support)
  4. Logical stop-loss would be wider than 1.5x ATR → SKIP (risk too large)
  5. volume_trend is "contracting" AND vol_ratio_6bar_5min < 1.5 → SKIP (setup losing steam)

CONFIDENCE THRESHOLDS:
  confidence 1–2 → decision must be SKIP, always
  confidence 3   → ENTER allowed only if no red_flags (or red_flags = "none")
  confidence 4–5 → ENTER allowed even with minor red_flags

VIX OVERLAY:
  india_vix > 18 → require confidence >= 4 to ENTER
  india_vix > 25 → this cycle should not have been called (Layer 3 blocks it),
                   but if it is, return SKIP for all stocks

RELATIVE STRENGTH FILTER:
  For LONG: if stock_vs_nifty_5day_pct < 0 → downgrade confidence by 1 and note in red_flags
  For SHORT: if stock_vs_nifty_5day_pct > 0 → downgrade confidence by 1 and note in red_flags

─── RED FLAG DETECTION ──────────────────────────────────────────────────────────
Always check for and report any of the following as red_flags (comma-separated string):
  - "volume declining": last candle volume is lower than both of the previous two candles
  - "weak candle body": candle_strength_last < 0.4 (indecision, potential reversal)
  - "near resistance": distance_to_resistance_pct < 0.5% for LONG (crowded exit above)
  - "near support": distance_to_support_pct < 0.5% for SHORT
  - "bearish market": nifty_trend_today is "bearish" for a LONG signal
  - "bullish market": nifty_trend_today is "bullish" for a SHORT signal
  - "sector underperforming": sector_vs_nifty_today_pct < -0.5% for LONG setups
  - "weak relative strength": stock_vs_nifty_5day_pct < 0 for LONG
  - "strong relative strength against short": stock_vs_nifty_5day_pct > 0 for SHORT
  - "extended daily RSI": rsi_14_daily > 72 for LONG (stock overbought on daily chart)
  - "oversold daily RSI": rsi_14_daily < 28 for SHORT (stock oversold on daily chart)
If none of the above are present, set red_flags to "none".

─── EXAMPLE OUTPUT ──────────────────────────────────────────────────────────────
[
  {
    "symbol": "TATAMOTORS",
    "decision": "ENTER",
    "direction": "LONG",
    "entry_price": 925.50,
    "stop_loss": 911.80,
    "target": 946.00,
    "confidence": 4,
    "reasoning": "Price is 0.82% above VWAP with RSI at 63 and strongly expanding volume, confirming genuine intraday momentum. The 2.3% 5-day outperformance of Nifty is the strongest single factor — relative strength is real and present.",
    "red_flags": "none"
  }
]
```

---

**User message format:**

```
Analyze these intraday trade setups for {today's date in DD-MMM-YYYY format}.
Market context: Nifty is {bullish/bearish/sideways} today ({nifty_change_pct}% from yesterday's close). India VIX is {value}.
Time of scan: {HH:MM} IST.

{paste the full JSON array from Layer 4 here}

Return a JSON array with exactly one decision object per stock, in the same order as input.
```

---

**Expected Claude response format:**

```json
[
  {
    "symbol": "TATAMOTORS",
    "decision": "ENTER",
    "direction": "LONG",
    "entry_price": 925.50,
    "stop_loss": 911.80,
    "target": 946.00,
    "confidence": 4,
    "reasoning": "Price is 0.82% above VWAP with RSI at 63 and strongly expanding volume confirming intraday momentum. The 2.3% 5-day outperformance of Nifty is the single most important factor — relative strength is real and present.",
    "red_flags": "none"
  }
]
```

---

**Parsing Claude's response:**
- Attempt `json.loads()` on the raw response text
- If parsing fails: strip any accidental backtick fences, then retry once
- If still fails: log the raw response, do not place any trade, send Telegram alert "Claude returned invalid JSON — no trades placed this cycle"
- For each stock in response:
  - If `decision == "ENTER"` AND `confidence >= 3`: proceed to order placement
  - If `decision == "SKIP"` OR `confidence < 3`: discard, log the reasoning
  - If `direction` in response does not match `signal_direction` that was sent: log as DIRECTION_MISMATCH, treat as SKIP regardless of decision field
  - If a stock present in the Layer 4 input is missing from Claude's response: treat as SKIP, log as MISSING_FROM_RESPONSE
- If the Claude API call itself fails or times out: log error, skip this cycle entirely, do not trade, send Telegram alert

---

## 4. Order Placement Logic (Updates to main.py and execution.py)

When Claude returns ENTER for a stock:

```
Step 1 — Freshness check:
  Verify last tick for this symbol was received within the last 30 seconds.
  If stale: skip this trade, log warning. Do not proceed.

Step 2 — Price proximity check:
  Verify current LTP is within 1.0% of Claude's suggested entry_price.
  (Using 1.0% — not 0.5% — because MARKET orders have inherent slippage.
  A 0.5% threshold would reject valid setups due to normal bid-ask spread movement.)
  If LTP has moved more than 1.0% away from entry_price: skip this trade, log "setup invalidated".
  Reason: The setup has materially changed. Do not chase.

Step 3 — Daily limit check:
  Verify user has not reached MAX_TRADES_PER_DAY.
  If limit reached: skip all remaining ENTER decisions for this user today.

Step 4 — Quantity calculation:
  per_trade_capital = user's total allocated capital / MAX_TRADES_PER_DAY
  quantity = floor(per_trade_capital / entry_price)
  Minimum quantity is 1. If quantity rounds to 0, skip trade and log warning.

Step 5 — Place entry order:
  Order type: MARKET (for speed of execution)
  After placement: apply Bug Fix 1 (verify actual fill status on timeout)
  Read actual filled_quantity from confirmation response (Bug Fix 2)

Step 6 — Place SL order:
  Use Claude's stop_loss price as the trigger price
  Quantity = filled_quantity from Step 5 (never the originally requested quantity)
  Order type: SL order on Upstox

Step 7 — Persist to database:
  Write to trades table:
    symbol, direction, entry_price (actual fill price from exchange),
    stop_loss, target, filled_quantity, sl_order_id (Bug Fix 3),
    confidence, reasoning, layer3_conditions_met,
    entry_time, status = "ACTIVE", paper_trade = false/true

Step 8 — Add to daily blacklist:
  Insert symbol into daily_blacklist for this user_id and today's date.
  This stock will not be scanned or traded again today regardless of future signals.

Step 9 — Send Telegram alert:
  "📈 [LONG/SHORT] ENTERED: {symbol} @ ₹{actual_fill_price}
   SL: ₹{sl} | Target: ₹{target} | Confidence: {n}/5
   Reason: {reasoning}"
```

---

## 5. Trade Monitoring Logic (trade_manager.py)

Run monitoring loop every 30 seconds for each active trade.

**This loop runs independently of the global kill switch (Bug Fix 7). Even when global trading is halted, this loop must continue to protect all open positions.**

```
Step 1 — Check feed freshness (Bug Fix 4):
  Verify last tick for this symbol was within the last 30 seconds.
  If stale: skip this monitoring cycle for this symbol, log warning.
  Do NOT exit a position based on stale data — wait for fresh data.

Step 2 — Get current LTP from tick cache (only proceeds if Step 1 passed).

Step 3 — Check 3:15 PM force exit FIRST (highest priority):
  If current time >= 15:15: exit position at MARKET price immediately.
  Log reason as "EOD forced exit". Proceed to Step 7.

Step 4 — Check stop-loss hit:
  LONG:  if LTP <= stop_loss → exit immediately at MARKET
  SHORT: if LTP >= stop_loss → exit immediately at MARKET
  If SL is hit, proceed to Step 7.

Step 5 — Check target hit:
  LONG:  if LTP >= target → exit immediately at MARKET
  SHORT: if LTP <= target → exit immediately at MARKET
  If target is hit, proceed to Step 7.

Step 6 — Trailing stop logic (only if Steps 3, 4, 5 did not trigger):
  Verify feed freshness again before modifying SL (prevents modifying SL on stale price).
  
  LONG:
    If price has moved >= 1x ATR_14_5min above entry price:
      Move SL to breakeven (entry price exactly). Only do this once.
    If price has moved >= 2x ATR_14_5min above entry price:
      Trail SL to (current LTP - 1x ATR_14_5min). Update on every 30-second cycle
      as long as price keeps rising.
    When updating SL:
      1. Cancel existing sl_order_id on Upstox
      2. Place new SL order at new stop_loss price
      3. Immediately update sl_order_id AND stop_loss in the database (Bug Fix 3)
      4. Update stop_loss in TradeManager memory
  
  SHORT: mirror logic exactly in reverse.

Step 7 — On any exit (SL hit / Target hit / EOD / manual):
  Place MARKET exit order on Upstox.
  Update trade record in database:
    exit_price (actual fill), exit_time, pnl, status = "CLOSED", exit_reason
  pnl = (exit_price - entry_price) * filled_quantity for LONG
        (entry_price - exit_price) * filled_quantity for SHORT
  Send Telegram alert:
    "✅ TRADE CLOSED: {symbol} [{reason: SL HIT / TARGET HIT / EOD / MANUAL}]
     Entry: ₹{entry} | Exit: ₹{exit} | P&L: ₹{pnl:+.2f}"

Step 8 — Consecutive loss check (runs after Step 7 for any closed trade):
  A "loss" is any trade where pnl < 0.
  A winning trade (pnl > 0 or pnl == 0) resets the consecutive loss counter to 0.
  After a losing trade: increment the consecutive_loss_counter for this user today.
  If consecutive_loss_counter >= 2:
    Set user's trading_enabled flag to false in the database for today.
    Send Telegram alert: "⛔ 2 consecutive losses for user {id}. Trading paused for today."
    User can manually re-enable from the dashboard if they choose to override this.
```

---

## 6. Daily Risk Controls

These are hard rules, not configuration suggestions. They must be enforced in code:

```
MAX_TRADES_PER_DAY       = 5     (enforced every scan cycle — Bug Fix 5)
MAX_CONSECUTIVE_LOSSES   = 2     (stops new entries after 2 consecutive losses)
NO_NEW_ENTRIES_AFTER     = 14:30 (2:30 PM IST — scanner runs but Layers 4+5 are skipped)
FORCE_EXIT_ALL_AT        = 15:15 (3:15 PM IST — all positions closed at MARKET)
WEBSOCKET_DISCONNECT_AT  = 15:25 (3:25 PM IST)
MAX_VIX_FOR_TRADING      = 25.0  (above this: no new entries at all)
CAUTIOUS_VIX_THRESHOLD   = 18.0  (above this: Claude requires confidence >= 4 to ENTER)
MAX_PRICE_SLIPPAGE_PCT   = 1.0   (skip entry if LTP moved >1.0% from Claude's entry price)
ONE_TRADE_PER_STOCK_DAY  = true  (blacklist symbol after first trade, per user)
LAYER4_MAX_STOCKS_PER_CYCLE = 5  (maximum stocks sent to Claude per cycle)
CANDLE_CACHE_REFRESH_MIN = 10    (refresh rolling candle cache every 10 minutes per symbol)
MARKET_DATA_REFRESH_MIN  = 15    (refresh Nifty trend and VIX every 15 minutes)
```

---

## 7. Daily Schedule — Full Timeline

```
08:55 AM — Verify Upstox API token validity. Attempt refresh if expired.
           If token refresh fails after 2 retries: send Telegram alert
           "⚠️ Token refresh failed. Bot cannot start. Manual intervention required."
           and abort startup entirely. Do not proceed to Layer 1.

09:00 AM — Layer 1: Fetch Nifty 500 daily OHLCV data. Run morning pre-filter.
           Save morning_watchlist to database (~80–120 stocks).
           Initialize active_watchlist in memory from morning_watchlist (full list starts active).
           Send Telegram: "✅ intradayBot started. Date: {date}. Watchlist: {N} stocks."

09:15 AM — WebSocket feed connects. Tick cache starts populating.

09:20 AM — First Layer 3 scan runs. Continuous 5-minute scan loop begins.

10:00 AM — Layer 2: First watchlist refresh.
11:00 AM — Layer 2: Second watchlist refresh.
12:00 PM — Layer 2: Third watchlist refresh.
01:00 PM — Layer 2: Fourth watchlist refresh.
02:00 PM — Layer 2: Fifth and final watchlist refresh.

02:30 PM — NO NEW ENTRIES after this time.
           Layer 3 scanner continues running (for monitoring stale watchlist awareness),
           but Layer 4 and Layer 5 are not called. No Claude API calls after 2:30 PM.

03:15 PM — Force exit all open positions at MARKET price.

03:25 PM — WebSocket feed disconnects.

03:30 PM — Fetch Nifty 50 closing price and India VIX closing value via REST API
           (WebSocket is disconnected; use Upstox REST quote API for these two values).
           Send Telegram daily summary (see Section 9).

03:35 PM — Write daily reconciliation log (see Section 10).
```

---

## 8. Telegram Alerting

Add Telegram bot notifications for all of the following events. Use direct HTTP calls to the Telegram Bot API (`https://api.telegram.org/bot{TOKEN}/sendMessage`). Store `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

**Events to alert:**

```
Bot startup (after Layer 1 completes):
  "✅ intradayBot started. Date: {date}. Watchlist: {N} stocks loaded."

Token refresh failure (before startup):
  "🚨 Upstox token refresh FAILED. Bot did not start. Manual login required."

Watchlist refresh:
  "🔄 Watchlist refreshed at {HH:MM}. Active stocks: {N}."

Trade entry (LONG):
  "📈 LONG ENTERED: {symbol} @ ₹{actual_fill_price}
   SL: ₹{sl} | Target: ₹{target} | Confidence: {n}/5
   Reason: {reasoning}"

Trade entry (SHORT):
  "📉 SHORT ENTERED: {symbol} @ ₹{actual_fill_price}
   SL: ₹{sl} | Target: ₹{target} | Confidence: {n}/5
   Reason: {reasoning}"

Trade exit:
  "✅ TRADE CLOSED: {symbol} [{reason}]
   Entry: ₹{entry} | Exit: ₹{exit} | P&L: ₹{pnl:+.2f}"

Stop trading — consecutive losses:
  "⛔ 2 consecutive losses for user {id}. Trading paused for today.
   Resume from dashboard if you choose to override."

Daily trade limit reached:
  "🔒 Daily trade limit ({MAX}) reached for user {id}. No more entries today."

VIX above threshold — cautious mode:
  "⚠️ India VIX at {value} — confidence threshold raised to 4 for new entries."

VIX too high — no new trades:
  "🚨 India VIX at {value} — above 25. No new entries until VIX drops."

Global kill switch activated:
  "🚨 GLOBAL HALT ACTIVATED by admin. All new entries stopped. Existing positions still monitored."

Global kill switch lifted:
  "✅ GLOBAL HALT LIFTED. Trading resumed for all users."

Order status unknown (Bug Fix 1):
  "🚨 ORDER STATUS UNKNOWN: {symbol}. Manual check required. Order ID: {order_id}
   No further orders placed for {symbol} today."

Stale feed warning (if stale for more than 2 consecutive monitoring cycles):
  "⚠️ Market feed stale for {N} minutes for {N_symbols} symbols. Check WebSocket."

Claude returned invalid JSON:
  "⚠️ Claude API returned invalid JSON at {HH:MM}. No trades placed this cycle."

Claude API call failed:
  "⚠️ Claude API call failed at {HH:MM} ({error_type}). Skipping this scan cycle."

Daily summary (3:30 PM):
  See Section 9.

Reconciliation complete (3:35 PM):
  See Section 10.
```

---

## 9. Daily Summary (3:30 PM Telegram Message)

Send this every trading day at 3:30 PM regardless of whether any trades were taken.

Nifty closing price and VIX are fetched via Upstox REST quote API at 3:30 PM (after WebSocket disconnects at 3:25 PM).

```
📊 DAILY SUMMARY — {DD-MMM-YYYY}

Trades taken: {N} / {MAX_TRADES_PER_DAY}
Winners: {N} | Losers: {N} | Breakeven: {N}
Win rate: {pct}%

Total P&L: ₹{amount:+.2f}
Best trade: {symbol} +₹{amount:.2f}
Worst trade: {symbol} -₹{amount:.2f}

Pipeline stats:
  Stocks scanned today: {N}
  Layer 3 signals fired: {N}
  Claude ENTER decisions: {N}
  Trades executed: {N}
  Claude calls made: {N}

Market close:
  Nifty: {closing_price} ({+/-pct}% today)
  India VIX: {closing_vix}
```

If no trades were taken today, still send the summary with all trade fields as 0 / "N/A".

---

## 10. Daily Reconciliation Log (3:35 PM)

After market close, reconcile the bot's database records against Upstox's official trade book:

```
For each trade recorded in the database today (status = "CLOSED"):
  - Fetch the actual trade from Upstox trade book API
  - Compare: entry price, exit price, filled quantity, P&L
  - If any field mismatches: log as RECONCILIATION_MISMATCH with full details

For ghost/orphan detection:
  - If trade exists in database but NOT in Upstox book: log as GHOST_TRADE
  - If trade exists in Upstox book but NOT in database: log as UNTRACKED_TRADE

Save full reconciliation result to database table: daily_reconciliation
Send Telegram: "📋 Reconciliation complete. Mismatches: {N} | Ghost: {N} | Untracked: {N}"
If any mismatches found: send a follow-up Telegram message with full mismatch details.
```

---

## 11. Database Changes Required

Run these scripts to update the schema. All are idempotent (safe to run multiple times):

```sql
-- ─── MODIFY EXISTING TRADES TABLE ────────────────────────────────────────────
ALTER TABLE trades
  ADD COLUMN IF NOT EXISTS sl_order_id VARCHAR(64) NULL,
  ADD COLUMN IF NOT EXISTS confidence INT NULL,
  ADD COLUMN IF NOT EXISTS reasoning TEXT NULL,
  ADD COLUMN IF NOT EXISTS layer3_conditions_met INT NULL,
  ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(50) NULL,
  ADD COLUMN IF NOT EXISTS paper_trade BOOLEAN DEFAULT FALSE;


-- ─── MORNING WATCHLIST ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS morning_watchlist (
  id                     INT AUTO_INCREMENT PRIMARY KEY,
  date                   DATE NOT NULL,
  symbol                 VARCHAR(20) NOT NULL,
  direction_bias         ENUM('LONG_CANDIDATE', 'SHORT_CANDIDATE') NOT NULL,
  vol_ratio_20day        FLOAT,
  relative_strength_5day FLOAT,
  price_vs_ema20_daily   FLOAT,
  price_vs_20day_high_pct FLOAT,
  created_at             DATETIME DEFAULT NOW(),
  UNIQUE KEY uq_morning_watchlist (date, symbol)
);


-- ─── ACTIVE WATCHLIST ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS active_watchlist (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  date             DATE NOT NULL,
  symbol           VARCHAR(20) NOT NULL,
  direction_bias   ENUM('LONG_CANDIDATE', 'SHORT_CANDIDATE') NOT NULL,
  added_at         DATETIME NOT NULL,
  removed_at       DATETIME NULL,
  removal_reason   VARCHAR(100) NULL,
  UNIQUE KEY uq_active_watchlist (date, symbol)
);


-- ─── DAILY BLACKLIST ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_blacklist (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  user_id          INT NOT NULL,
  date             DATE NOT NULL,
  symbol           VARCHAR(20) NOT NULL,
  blacklisted_at   DATETIME DEFAULT NOW(),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE KEY uq_daily_blacklist (user_id, date, symbol)   -- prevents duplicate entries
);


-- ─── SYSTEM CONFIG (GLOBAL KILL SWITCH + OTHER FLAGS) ────────────────────────
CREATE TABLE IF NOT EXISTS system_config (
  config_key   VARCHAR(100) PRIMARY KEY,
  config_value VARCHAR(255) NOT NULL,
  updated_at   DATETIME DEFAULT NOW() ON UPDATE NOW()
);

INSERT INTO system_config (config_key, config_value)
VALUES ('GLOBAL_TRADING_ENABLED', 'true')
ON DUPLICATE KEY UPDATE config_key = config_key;  -- idempotent: skip if already exists


-- ─── DAILY RECONCILIATION ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_reconciliation (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  date              DATE NOT NULL,
  total_trades      INT DEFAULT 0,
  mismatches        INT DEFAULT 0,
  ghost_trades      INT DEFAULT 0,
  untracked_trades  INT DEFAULT 0,
  details           TEXT,
  created_at        DATETIME DEFAULT NOW()
);
```

---

## 12. Dashboard Additions (FastAPI + existing UI)

Add these elements to the existing dashboard without modifying any existing UI elements:

```
1. Global status banner — fixed at the very top of every page:
   - Green banner: "✅ System Active — Trading Enabled"
   - Red banner:   "🚨 SYSTEM HALTED — No New Entries. Positions Still Monitored."
   - Admin buttons: [Halt All Trading] [Resume Trading]
   - Note: "Resume" button only visible/active when system is halted.
   - Last updated timestamp: "Status as of {HH:MM:SS}"

2. Today's watchlist panel:
   - Morning watchlist count: {N} stocks
   - Active watchlist count: {N} stocks (label: "updated {HH:MM}")
   - Collapsed list of active symbols (expandable)

3. Live signal log (scrollable, last 50 entries):
   - Every Layer 3 signal that fires: [{HH:MM}] {symbol} {LONG/SHORT} — {N}/5 conditions met
   - Every Claude decision: [{HH:MM}] {symbol} → ENTER/SKIP (confidence: {n}/5) — {reasoning}
   - Color coding: ENTER decisions in green, SKIP in grey

4. Feed health indicator (shown near top of page, updates every 10 seconds):
   - Green dot: all monitored symbols have ticks within the last 15 seconds
   - Yellow dot: some symbols have ticks between 15 and 30 seconds old
     NOTE: At yellow status, the scanner is still running but some signals may be
     skipped due to stale data. User should monitor closely.
   - Red dot:   majority of symbols have ticks older than 30 seconds, OR WebSocket
     is disconnected. At red status, no new trades can be entered (stale data check
     in Layer 3 will block them). This effectively means trading is paused until
     the feed recovers.
   The distinction: yellow = degraded but partially trading; red = stale enough
   that trading is functionally blocked by the 30-second freshness rule.

5. Paper trading indicator:
   - If PAPER_TRADING_MODE = true, show a persistent orange banner:
     "📄 PAPER TRADING MODE — No real orders are being placed"
```

---

## 13. Summary — What Gets Replaced vs Kept

| Component | Action |
|---|---|
| `strategy.py` | DELETE — replaced by Layers 1, 2, 3 in this document |
| `ai_filter.py` | DELETE — replaced by Layers 4 and 5 in this document |
| `execution.py` | MODIFY — apply Bug Fix 1 and Bug Fix 2 |
| `trade_manager.py` | MODIFY — apply Bug Fix 3; add full trailing stop logic from Section 5 |
| `market_feed.py` | MODIFY — apply Bug Fix 4 (add timestamp to every tick cache entry) |
| `main.py` | MODIFY — apply Bug Fix 5, 6, 7; integrate full pipeline schedule from Section 7 |
| `database.py` | MODIFY — add new tables and columns from Section 11 |
| `credential_service.py` | KEEP — no changes |
| `crypto_utils.py` | KEEP — no changes |
| `auth.py` | KEEP — no changes |
| `auth_middleware.py` | KEEP — no changes |
| FastAPI dashboard | MODIFY — add elements from Section 12 only, no existing UI changes |
| Multi-tenant UserBot structure | KEEP — no changes to per-user isolation design |

---

## 14. Important Implementation Notes for Antigravity

1. **API key storage:** The Claude API key (`ANTHROPIC_API_KEY`) must be stored in the `.env` file alongside `CREDENTIAL_ENCRYPTION_KEY` and `UPSTOX_API_KEY`. Never hardcode any keys.

2. **Async architecture:** All Layer 1 through Layer 5 logic runs inside each user's existing `UserBot` async task. Do not create a separate process or thread. Use the existing per-user task structure.

3. **Non-blocking sleep:** The 5-minute scan loop must use `await asyncio.sleep(300)` between cycles. Never use blocking `time.sleep()`. The bot is fully async — keep it that way.

4. **Batch Claude calls:** When multiple stocks pass Layer 3 in the same cycle, batch ALL of them into one single Claude API call (max 5 stocks). Never make one Claude API call per stock.

5. **Blacklist persistence:** The daily blacklist (one trade per stock per day) must persist in the database with a UNIQUE constraint. On startup, reload today's blacklist from the database before the first scan cycle. Do not rely on in-memory state alone.

6. **Active watchlist persistence:** The active watchlist must also persist in the database. On restart, reload today's active watchlist from the database rather than waiting for the next hourly refresh. If no active watchlist exists in the database for today (e.g., morning restart before 10 AM), initialize from the morning_watchlist.

7. **Entry cutoff:** Never place a new entry order at or after 2:30 PM. The Layer 3 scanner may still run for awareness, but Layer 4 and Layer 5 are not called after 2:30 PM.

8. **Monetary precision:** All P&L values stored in the database must be in rupees with 2 decimal places (DECIMAL(12,2)). Never round during calculation — only round for display output.

9. **Paper trading mode:** Add `PAPER_TRADING_MODE = true/false` to config. When true: all pipeline logic runs exactly as described, candle data is fetched, Claude is called, decisions are made, but no real orders are placed on Upstox. Instead, log "PAPER TRADE WOULD ENTER: {symbol} @ ₹{price}". Simulated trades ARE written to the `trades` table with `paper_trade = true`. P&L is calculated against real live prices from the tick cache. The daily summary and reconciliation run normally for paper trades, comparing simulated fills against real prices.

10. **Consecutive loss counter:** The counter is per-user, per-day, in-memory. A losing trade increments it. A winning trade resets it to 0. On bot restart mid-day, recalculate the counter from today's closed trades in the database (count consecutive losses from the most recent trade backward).

11. **Market data fallback at 3:30 PM:** The WebSocket disconnects at 3:25 PM. The Nifty closing price and VIX value needed for the daily summary at 3:30 PM must be fetched via Upstox REST quote API at that time. Cache these values before WebSocket disconnect if possible, or fetch fresh via REST at 3:30 PM.
