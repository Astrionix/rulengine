"""
nifty500_instruments.py — Nifty 500 Universe & Sector Mapping
──────────────────────────────────────────────────────────────
Provides the full instrument mapping for Layer 1 pre-filter.
Uses the existing Config.SYMBOLS_MAPPING as the base, with sector information.

NOTE: The current universe uses the ~80 stocks already in Config.SYMBOLS_MAPPING.
For a full Nifty 500 universe, this file should be expanded with additional
instrument keys from Upstox. The design can be updated to dynamically fetch
the Nifty 500 constituent list from an API or CSV if available.
"""

from config import Config


# ─── Sector Classification ──────────────────────────────────────────────────
# Standard NSE sector categorization for stocks in the universe.
# This is used by Layer 4 feature extraction for sector_vs_nifty_today_pct.

SECTOR_MAP = {
    # Banking & Financial Services
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "AXISBANK": "Banking", "KOTAKBANK": "Banking", "INDUSINDBK": "Banking",
    "PNB": "Banking", "CANBK": "Banking", "IDFCFIRSTB": "Banking",
    "YESBANK": "Banking",
    
    # NBFC / Financial Services
    "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services",
    "CHOLAFIN": "Financial Services", "MUTHOOTFIN": "Financial Services",
    "PFC": "Financial Services", "RECLTD": "Financial Services",
    "JIOFIN": "Financial Services", "IRFC": "Financial Services",
    "SBILIFE": "Insurance", "HDFCLIFE": "Insurance", "ICICIPRULI": "Insurance",
    "LICI": "Insurance",
    
    # IT / Technology
    "INFY": "IT", "TCS": "IT", "HCLTECH": "IT", "WIPRO": "IT",
    "TECHM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    
    # Oil & Gas / Energy
    "RELIANCE": "Oil & Gas", "ONGC": "Oil & Gas", "IOC": "Oil & Gas",
    "BPCL": "Oil & Gas", "GAIL": "Oil & Gas", "ADANIGREEN": "Power",
    "NTPC": "Power", "POWERGRID": "Power", "ADANIPOWER": "Power",
    "JSWENERGY": "Power",
    
    # Telecom
    "BHARTIARTL": "Telecom",
    
    # FMCG
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "MARICO": "FMCG", "GODREJCP": "FMCG",
    "TATACONSUM": "FMCG", "VBL": "FMCG", "DMART": "FMCG",
    
    # Auto
    "MARUTI": "Auto", "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "OLAELEC": "Auto",
    
    # Metals & Mining
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "COALINDIA": "Metals",
    
    # Pharma / Healthcare
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "APOLLOHOSP": "Healthcare",
    "MAXHEALTH": "Healthcare",
    
    # Infrastructure / Construction
    "LT": "Infrastructure", "ADANIENT": "Infrastructure",
    "ADANIPORTS": "Infrastructure", "CONCOR": "Infrastructure",
    "DLF": "Realty", "LODHA": "Realty",
    
    # Cement
    "ULTRACEMCO": "Cement", "SHREECEM": "Cement", "AMBUJACEM": "Cement",
    "GRASIM": "Cement",
    
    # Consumer Durables / Retail
    "TITAN": "Consumer", "TRENT": "Consumer", "HAVELLS": "Consumer",
    "ASIANPAINT": "Consumer", "PIDILITIND": "Consumer", "POLYCAB": "Consumer",
    
    # Capital Goods / Defence
    "HAL": "Defence", "BEL": "Defence", "SIEMENS": "Capital Goods",
    "ABB": "Capital Goods",
    
    # Chemicals
    "SRF": "Chemicals",
    
    # Telecom / Media
    "SUNTV": "Media", "TATACOMM": "Telecom",
    
    # Transport
    "INDIGO": "Aviation",
    
    # Others / Diversified
    "AU SMALL": "Banking",
    "M&M": "Auto",
}


def get_universe() -> dict:
    """Returns the full trading universe: {symbol_name: instrument_key}.
    Currently uses Config.SYMBOLS_MAPPING. Can be expanded to Nifty 500."""
    return dict(Config.SYMBOLS_MAPPING)


def get_sector(symbol: str) -> str:
    """Return the sector for a given symbol. Defaults to 'Others' if not mapped."""
    return SECTOR_MAP.get(symbol, "Others")


def get_sectors_mapping() -> dict:
    """Return the full sector mapping dictionary."""
    return dict(SECTOR_MAP)


def get_symbols_by_sector(sector: str) -> list:
    """Return all symbols in a given sector."""
    return [sym for sym, sec in SECTOR_MAP.items() if sec == sector]


def get_all_sectors() -> list:
    """Return all unique sector names."""
    return sorted(set(SECTOR_MAP.values()))
