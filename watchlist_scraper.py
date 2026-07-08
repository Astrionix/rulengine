"""
watchlist_scraper.py — Fail-safe external breakout/volume scraper for Layer 1.
Fetches top volume gainers, price gainers, and price losers from the NSE India live analysis API.
Gracefully handles timeouts, rate limits, and network errors.
"""

import logging
import requests

logger = logging.getLogger(__name__)

# Standard browser headers to bypass NSE India automated access blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/"
}

def fetch_breakout_stocks() -> list:
    """
    Query NSE Live Analysis endpoints to fetch the day's active breakouts.
    Returns a list of dicts: [{"symbol": str, "direction_bias": str}]
    """
    logger.info("[Scraper] Starting external breakout scraping from NSE India...")
    scraped = []
    
    session = requests.Session()
    # Establish session and retrieve initial cookies from NSE homepage
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
    except Exception as e:
        logger.error(f"[Scraper] Failed to establish NSE session cookies: {e}")
        return []

    # 1. Fetch Volume spurts (Most Active Securities by Volume)
    try:
        vol_url = "https://www.nseindia.com/api/live-analysis-most-active-securities?index=volume"
        r = session.get(vol_url, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("data", [])
            for item in rows:
                symbol = item.get("symbol")
                p_change = item.get("pChange", 0.0)
                if symbol:
                    # Determine bias based on price change
                    bias = "LONG_CANDIDATE" if p_change >= 0 else "SHORT_CANDIDATE"
                    scraped.append({"symbol": symbol, "direction_bias": bias})
            logger.info(f"[Scraper] Loaded {len(rows)} volume active stocks from NSE.")
        else:
            logger.warning(f"[Scraper] Volume active endpoint returned status {r.status_code}")
    except Exception as e:
        logger.error(f"[Scraper] Error fetching volume active stocks: {e}")

    # 2. Fetch Price Gainers (Bullish breakouts)
    try:
        gainers_url = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
        r = session.get(gainers_url, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            all_sec = data.get("allSec", {})
            rows = all_sec.get("data", [])
            for item in rows:
                symbol = item.get("symbol")
                if symbol:
                    scraped.append({"symbol": symbol, "direction_bias": "LONG_CANDIDATE"})
            logger.info(f"[Scraper] Loaded {len(rows)} price gainers from NSE.")
        else:
            logger.warning(f"[Scraper] Price gainers endpoint returned status {r.status_code}")
    except Exception as e:
        logger.error(f"[Scraper] Error fetching price gainers: {e}")

    # 3. Fetch Price Losers (Bearish breakouts)
    try:
        losers_url = "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
        r = session.get(losers_url, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            all_sec = data.get("allSec", {})
            rows = all_sec.get("data", [])
            for item in rows:
                symbol = item.get("symbol")
                if symbol:
                    scraped.append({"symbol": symbol, "direction_bias": "SHORT_CANDIDATE"})
            logger.info(f"[Scraper] Loaded {len(rows)} price losers from NSE.")
        else:
            logger.warning(f"[Scraper] Price losers endpoint returned status {r.status_code}")
    except Exception as e:
        logger.error(f"[Scraper] Error fetching price losers: {e}")

    # Deduplicate symbols (giving preference to price variation scans over volume scans)
    deduped = {}
    for item in scraped:
        sym = item["symbol"]
        bias = item["direction_bias"]
        # If symbol already exists, price variation bias overrides default volume bias
        if sym not in deduped:
            deduped[sym] = bias
        elif bias != "LONG_CANDIDATE" and deduped[sym] == "LONG_CANDIDATE":
            # Keep LONG if already long
            pass
        else:
            deduped[sym] = bias

    res = [{"symbol": k, "direction_bias": v} for k, v in deduped.items()]
    logger.info(f"[Scraper] Deduplication complete. Scraped total of {len(res)} unique symbols.")
    return res
