"""
pipeline_layer4_5.py -- Python Mandatory Pre-Filter (Layer 4.5)
Sits between Layer 4 (feature extraction) and Layer 5 (Claude API).
Runs all DETERMINISTIC mandatory skip checks before any signal reaches Claude.

Rules moved here from Claude's system prompt:
  - VWAP direction check
  - Support/Resistance proximity check (< 0.3%)
  - Volume contracting check
  - VIX > 25 hard block
  - BankNifty strong counter-trend (> 0.30% against direction)
"""

import logging
logger = logging.getLogger(__name__)


def python_mandatory_filter(payloads: list) -> tuple:
    """
    Run deterministic mandatory skip checks on a list of Layer 4 payloads.

    Returns:
        (approved, rejected) where:
          approved -- list of payloads that pass all checks (send to Claude)
          rejected -- list of dicts: {symbol, direction, reason}
    """
    approved = []
    rejected = []

    for p in payloads:
        symbol    = p.get("symbol", "UNKNOWN")
        direction = p.get("signal_direction", "")
        price_ctx = p.get("price_context", {})
        levels    = p.get("levels", {})
        volume    = p.get("volume", {})
        mkt       = p.get("market_context", {})

        ltp         = price_ctx.get("ltp", 0.0)
        atr         = price_ctx.get("atr_14_5min", 0.0)
        vwap_pct    = price_ctx.get("price_vs_vwap_pct", None)
        dist_to_res = levels.get("distance_to_resistance_pct", 999.0)
        dist_to_sup = levels.get("distance_to_support_pct", 999.0)
        vol_trend   = volume.get("volume_trend", "")
        vol_ratio   = volume.get("vol_ratio_6bar_5min", 0.0)
        vix         = mkt.get("india_vix", 0.0)
        bn_slope    = mkt.get("bank_nifty_slope", "flat")
        bn_change   = mkt.get("bank_nifty_change_3bar_pct", 0.0)

        skip_reason = None

        # Guard: missing critical data
        if not ltp or not atr or not direction:
            skip_reason = "Incomplete data: ltp/atr/direction missing"

        # Rule 1: VIX > 25 -> hard block all signals
        elif vix > 25.0:
            skip_reason = "VIX={:.1f} > 25 -- extreme volatility blocked".format(vix)

        # Rule 2: VWAP direction check
        elif vwap_pct is not None:
            if direction == "LONG" and vwap_pct <= 0:
                skip_reason = "Price at/below VWAP ({:.2f}%) for LONG".format(vwap_pct)
            elif direction == "SHORT" and vwap_pct >= 0:
                skip_reason = "Price at/above VWAP ({:.2f}%) for SHORT".format(vwap_pct)

        # Rule 3: Support/Resistance proximity < 0.3%
        if not skip_reason:
            if direction == "LONG" and dist_to_res < 0.30:
                skip_reason = "Too close to resistance ({:.2f}% below 0.30%)".format(dist_to_res)
            elif direction == "SHORT" and dist_to_sup < 0.30:
                skip_reason = "Too close to support ({:.2f}% below 0.30%)".format(dist_to_sup)

        # Rule 4: Volume contracting
        if not skip_reason:
            if vol_trend == "contracting" and vol_ratio < 1.5:
                skip_reason = "Volume contracting (ratio={:.1f}x below 1.5)".format(vol_ratio)

        # Rule 5: BankNifty strong counter-trend (hard skip, not just downgrade)
        if not skip_reason:
            if direction == "LONG" and bn_slope == "falling" and bn_change < -0.30:
                skip_reason = "BankNifty strongly falling ({:.2f}%) -- LONG blocked".format(bn_change)
            elif direction == "SHORT" and bn_slope == "rising" and bn_change > 0.30:
                skip_reason = "BankNifty strongly rising ({:.2f}%) -- SHORT blocked".format(bn_change)

        # Result
        if skip_reason:
            logger.info("[Pre-Filter] {} {} SKIPPED -- {}".format(symbol, direction, skip_reason))
            rejected.append({"symbol": symbol, "direction": direction, "reason": skip_reason})
        else:
            logger.debug("[Pre-Filter] {} {} APPROVED -- all mandatory checks passed".format(symbol, direction))
            approved.append(p)

    return approved, rejected
