"""
test_claude_axisbank_today.py
─────────────────────────────
Replays today's AXISBANK ORB SHORT signal through the exact same
Claude (Layer 5) analysis the bot would have run at 10:40 AM.

Copy to c:\\Users\\jayadeep\\intradayBot\\ then run:
    python test_claude_axisbank_today.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_layer5 import ClaudeAnalyzer
import json

# ─── Today's AXISBANK signal data (from bot logs at 10:40 AM) ─────────────────
#
# [ORB] 🔔 BREAKOUT: AXISBANK SHORT @ ₹1301.90
# ORB H=1312.60 / L=1304.80  (range = 0.6%)
# Vol = 2.2x  |  SL=₹1315.23  |  Tgt=₹1280.04  |  R:R=2.0x
#
# atr approx = (1312.60 - 1304.80) / 3  = 2.60
# ──────────────────────────────────────────────────────────────────────────────

orb_payload = {
    "symbol": "AXISBANK",
    "signal_direction": "SHORT",
    "signal_time": "10:40",
    "time_bucket": "MORNING",
    "layer3_conditions_met": 5,
    "target_multiplier": 2.0,

    "price_context": {
        "ltp": 1301.90,
        "atr_14_5min": 2.60,
        "atr_pct": 0.20,
        "price_vs_vwap_pct": 0.0,   # bot hardcodes 0.0 for ORB → change to test
        "price_vs_ema20_daily_pct": 0.0,
        "price_vs_20day_high_pct": 0.0,
    },

    "momentum": {
        "rsi_14_5min": 60.0,
        "rsi_14_daily": 55.0,
        "ema20_5min_slope": "falling",
        "candle_strength_last": 1.0,
        "price_slope_10bar": 0,
    },

    "volume": {
        "vol_ratio_20day_daily": 1.0,
        "vol_ratio_6bar_5min": 2.2,
        "volume_trend": "expanding",
    },

    "levels": {
        "support_20bar_5min": 1304.80,
        "resistance_20bar_5min": 1312.60,
        "distance_to_resistance_pct": abs(1312.60 - 1301.90) / 1301.90 * 100,  # 0.82%
        "distance_to_support_pct":    abs(1301.90 - 1304.80) / 1301.90 * 100,  # 0.22%  ← PROBLEM
        "previous_day_high": 1318.00,
        "previous_day_low":  1284.00,
        "cleared_previous_day_high": False,
        "cleared_previous_day_low": True,
    },

    "market_context": {
        "nifty_trend_today": "bearish",
        "bank_nifty_slope": "falling",
        "bank_nifty_change_3bar_pct": -0.15,
        "india_vix": 14.0,
        "sector": "Banking",
        "sector_vs_nifty_today_pct": -0.30,
        "stock_vs_nifty_5day_pct": -0.80,
        "market_regime": "TRENDING_DOWN",
    },

    "recent_candles_5min": [],

    "orb_meta": {
        "orb_high": 1312.60,
        "orb_low":  1304.80,
        "or_range_pct": 0.60,
        "entry_price": 1303.50,
        "stop_loss":   1315.23,
        "target":      1280.04,
        "rr_ratio":    2.0,
        "strategy":    "ORB",
    },
}

print("=" * 70)
print("AXISBANK ORB SHORT @ Rs.1301.90 — Claude Layer 5 Analysis Replay")
print("Signal Time: 10:40 AM, 16-Jul-2026")
print("=" * 70)
print()

# ── Key flag to watch: distance_to_support_pct = 0.22% ─────────────────────
# Rule 3 in Claude system prompt:
#   "For SHORT: distance_to_support_pct < 0.3% → SKIP (stock is against support)"
# 0.22% < 0.30% → Claude should SKIP this trade on mandatory rule 3
print("⚠️  PRE-CHECK: distance_to_support_pct = {:.2f}%".format(
    abs(1301.90 - 1304.80) / 1301.90 * 100
))
print("   Claude mandatory skip rule: SHORT with dist_to_support < 0.3% → SKIP")
print("   This means LTP (1301.90) is only 2.9 rupees below the ORB low (1304.80)")
print("   which is the SUPPORT level for this SHORT. Too close → high reversal risk.")
print()

print("Calling Claude API now...")
print("-" * 70)

analyzer = ClaudeAnalyzer()
decisions = analyzer.analyze(
    feature_payloads=[orb_payload],
    nifty_trend="bearish",
    nifty_change_pct=-0.45,
    vix_value=14.0,
)

print()
print("=" * 70)
print("CLAUDE DECISION:")
print("=" * 70)
if decisions:
    d = decisions[0]
    print(f"  Symbol    : {d.get('symbol')}")
    print(f"  Decision  : {d.get('decision')}  <── ENTER or SKIP")
    print(f"  Direction : {d.get('direction')}")
    print(f"  Confidence: {d.get('confidence')} / 5")
    print(f"  Reasoning : {d.get('reasoning')}")
    print(f"  Red Flags : {d.get('red_flags')}")
    print()
    if d.get("decision") == "ENTER":
        print("✅ Claude would have APPROVED this trade.")
    else:
        print("❌ Claude would have SKIPPED this trade.")
else:
    print("⚠️ No decision returned — check ANTHROPIC_API_KEY in config.py")
