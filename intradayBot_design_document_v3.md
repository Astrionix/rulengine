# IntradayBot — Complete System Design Document (v3)

---

## 1. Project Context & Philosophy
This is an automated Python intraday momentum trading bot connecting to the Upstox API.
**Tech Stack:** Python, FastAPI, MySQL, Upstox API, Claude API (Anthropic, `claude-sonnet-5`).
**Core Philosophy:** The system scans the Nifty 500 for intraday momentum breakouts and breakdowns, mathematically verifies structure and risk-reward, and uses Claude strictly as a final visual/structural setup validator before execution.

---

## 2. The 5-Layer Continuous Pipeline

### Layer 1: The Hybrid Discovery Engine (9:00 AM, daily)
**Purpose:** Maintain a dynamic, ever-evolving watchlist of high-momentum stocks.

#### A. Morning Pre-Filter (9:00 AM)
Scans historical daily candles (45 days) for all Nifty 500 stocks. A stock must pass all of:
- **Volume Activity:** `Yesterday's volume ÷ 20-day avg daily volume >= 1.3x`
- **Trend Filter (Daily EMA):** `Close > 20-day EMA` (LONG) or `< 20-day EMA` (SHORT)
- **Breakout Proximity:** Distance from 20-day high/low `<= 8%` away (not extended)
- **Relative Strength:** Stock's 5-day return vs Nifty 50's 5-day return must beat/lag in trade direction.

#### B. Intraday Dynamic Discovery (Every 15 mins)
Runs in the background (9:15 AM to 2:30 PM). 
- Any stock surging `>2.0%` from the open on high relative volume is instantly injected into the active watchlist.

---

### Layer 2: The Active Watchlist Manager (hourly + 10-min drift check)
**Purpose:** Keeps the live-tracked pool lean to conserve WebSocket bandwidth.
- **Drop Rule:** Drops stocks that are simultaneously: RSI(14) between 44–56 (sideways), 3-candle volume below the 20-bar average, and a flat 10-candle price slope.
- **Add Rule:** Adds stocks from the morning list (or freshly scraped breakouts) that show a sudden 3-candle volume surge (`>= 2x` the 20-bar average).
- **Sorting Logic:** Evaluates the current watchlist, selecting the top 50 LONGs (% gain) and top 50 SHORTs (% drop).

---

### Layer 3: The Quantitative Signal Scanner (every 5 minutes, 9:20 AM–2:30 PM)
**Purpose:** Identify precise entry triggers using a continuous 5-minute loop. Pure rule-based, no external API calls.
Evaluates the following conditions on the most recently completed 5-minute candle:

1. **Volume Expansion:** Candle volume `>= 2x` the 6-bar average.
2. **VWAP Reclaim:** Price `> +0.15%` above VWAP (LONG) / `< -0.15%` below VWAP (SHORT).
3. **Momentum (RSI-14):** `58–75` (LONG) / `25–42` (SHORT).
4. **Micro-Trend Alignment:** 9 EMA vs 21 EMA in the trade's direction.
5. **Candle Strength:** Body/range ratio `>= 0.50` (closes near high/low, minimal wick).

**Dynamic Threshold:** Normally **3/5** conditions required. Tightens to **4/5** during lunch hour (12:00–1:30 PM) or on a classified `LOW_PARTICIPATION` day.

**Additional Filters Layered on Top:**
- **Climax Volume Skip (LONG only):** If candle volume `>= 5x` the 20-bar average, skip (treated as exhaustion).
- **15-Minute Multi-Timeframe Alignment:** 15-min EMA20 must be sloping in the trade direction, and price must be on the correct side of the 15-min VWAP.
- **Sector Momentum Filter:** LONGs skipped if stock's sector index is `< -0.3%` (and vice versa).

---

### Layer 4: Mathematical Target & Risk Engine
**Purpose:** Deterministically computes trade geometry before Claude sees it.

#### 1. Adaptive Target Multiplier (Market Regime)
Regime classification is re-evaluated at 9:30 AM and hourly thereafter.
- **Trending (Up/Down):** `2.5x`
- **Ranging / High Volatility:** `1.5x`
- **Default:** `2.0x`

#### 2. Entry & Stop-Loss Calculation
- **Entry:** `LTP ± 0.05%` slippage buffer.
- **Stop-Loss:** 20-bar structural support/resistance `±` a small buffer.
  - If distance is *too tight* (`< 0.3%` of price), widen to `1x ATR(14)`.
  - If distance is *too wide* (`> 1.5x ATR(14)`), **reject the trade outright**.

#### 3. Target Generation
- **Target Price:** `Entry ± (multiplier × risk)`.
- **Constraint:** Target is rejected if it lands beyond the 20-bar resistance/support — *unless* volume is confirming a genuine breakout (`6-bar vol ratio > 3.0` and volume trend expanding), in which case the target is capped just past the level (`0.5%`).

---

### Layer 5: AI Setup Validation (Claude)
**Purpose:** Validates structure. It does not calculate price levels.
- Claude receives the rich Layer 4 payload (RSI, EMA slope, VWAP, Nifty trend, 20-candle OHLCV).
- Checks for: Trend consistency (higher-highs/higher-lows), consolidation/compression patterns preceding the breakout, and visual red flags (e.g. large wicks).
- **Decision Matrix:** 
  - `Confidence 1-2`: Automatic SKIP.
  - `Confidence 3`: ENTER only if 0 red flags.
  - `Confidence 4-5`: ENTER.

**Local Rule-Based Fallback (`run_local_rule_analysis`):**
A parallel, pure-Python rule engine mirrors Claude's logic (ATR risk check, volume-contraction check, Nifty trend scoring). It runs in shadow mode on every AI-approved trade to measure Claude's actual value-add.

---

## 3. Parallel Strategy — Opening Range Breakout (ORB)
Alongside the 5-layer momentum pipeline, a separate ORB sub-strategy exists:
- Observes the **9:15–9:30 AM** opening range for each active-watchlist stock.
- Scans for breakouts of that range from **9:30–11:00 AM** (1-minute cadence).
- Blocked if they contradict the day's classified Nifty regime (e.g., no ORB LONGs on a bearish day).

---

## 4. Position Sizing & Execution
- **Risk-Based Sizing:** `quantity = floor((DAILY_LOSS_LIMIT / MAX_TRADES_PER_DAY) / |Entry - SL|)`
- **Leverage:** 5x intraday (Upstox CO/MIS), validated per-symbol before every order.
- **VIX-Based Position Sizing:** Quantity scales down as VIX rises: 100% (<13), 80% (13-16), 60% (16-20), 40% (20-25). Trading is **blocked entirely at VIX >= 25**.
- **Slippage Guard:** Order is skipped if live price moved beyond tolerance from Entry (`0.3%` for Momentum, `0.5%` for ORB).
- **Partial-Fill Safety:** SL is placed strictly on `filled_quantity`, not the requested quantity.
- **Broker-Rejection Fallback:** If rejected (e.g. insufficient margin), Hybrid Virtual Mode kicks in to track it as a virtual position.

---

## 5. Exit Management & Risk Controls
- **Stop-Loss Recovery:** SL order ID is immediately written to the database so it survives a server crash.
- **Trailing Stop:** Once profit reaches `1R`, the bot begins trailing using an adaptive ATR-based trail (looser multiple in strong trends, tighter in weak ones).
- **Time-Based Exits:** No new entries after **2:30 PM**. All open positions force-flattened at **3:15 PM**.

**Kill Switches & Caps:**
- **Max Trades/Day:** Hard DB-enforced cap (default 5).
- **Global Halt Flag:** Admin switch; blocks new entries instantly while existing positions continue to be monitored.
- **Stale-Data Guard:** Any tick older than 30 seconds disqualifies that stock from being scanned.
- **Sector Concentration:** Max 2 trades per sector at once.
