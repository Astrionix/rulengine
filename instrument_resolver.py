# import os
# import gzip
# import json
# import logging
# import requests
# from datetime import datetime, date
# import pytz
# from config import Config

# logger = logging.getLogger(__name__)
# IST = pytz.timezone('Asia/Kolkata')

# CACHE_FILE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "nse_instruments.json.gz"))
# INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# _symbols_map = {}

# def _get_file_modification_date(filepath: str) -> date:
#     """Return the modification date of a file in IST timezone."""
#     mtime = os.path.getmtime(filepath)
#     dt = datetime.fromtimestamp(mtime, IST)
#     return dt.date()

# def download_and_cache_instruments(force: bool = False) -> bool:
#     """
#     Downloads the Upstox NSE instruments list and caches it locally as a gzipped JSON file.
#     Only downloads if file does not exist, or the file is outdated (modified on a previous date).
#     """
#     now = datetime.now(IST)
#     today = now.date()

#     # Check if download is required
#     if not force and os.path.exists(CACHE_FILE_PATH):
#         try:
#             mod_date = _get_file_modification_date(CACHE_FILE_PATH)
#             if mod_date == today:
#                 logger.info("[InstrumentResolver] Cached instrument file is up-to-date. Skipping download.")
#                 return True
#         except Exception as e:
#             logger.error(f"[InstrumentResolver] Error checking cache file modification date: {e}")

#     logger.info(f"[InstrumentResolver] Downloading instruments list from {INSTRUMENTS_URL}...")
#     try:
#         response = requests.get(INSTRUMENTS_URL, timeout=30)
#         if response.status_code == 200:
#             with open(CACHE_FILE_PATH, "wb") as f:
#                 f.write(response.content)
#             logger.info(f"[InstrumentResolver] Successfully cached instruments list to {CACHE_FILE_PATH}")
#             return True
#         else:
#             logger.error(f"[InstrumentResolver] Failed to download instruments. Status code: {response.status_code}")
#             return False
#     except Exception as e:
#         logger.error(f"[InstrumentResolver] Error during instrument download: {e}")
#         return False

# def load_instruments() -> dict:
#     """
#     Loads the cached instruments and returns a mapping of trading_symbol -> instrument_key.
#     Falls back to Config.SYMBOLS_MAPPING if loading fails.
#     """
#     global _symbols_map
#     if _symbols_map:
#         return _symbols_map

#     # Ensure file is downloaded and cached
#     download_and_cache_instruments()

#     if not os.path.exists(CACHE_FILE_PATH):
#         logger.warning("[InstrumentResolver] Cache file not found. Falling back to Config.SYMBOLS_MAPPING.")
#         return dict(Config.SYMBOLS_MAPPING)

#     logger.info(f"[InstrumentResolver] Loading instruments from cache: {CACHE_FILE_PATH}...")
#     try:
#         with gzip.open(CACHE_FILE_PATH, "rt", encoding="utf-8") as f:
#             data = json.load(f)
        
#         # Build lookup table for standard NSE equities (NSE_EQ segment and EQ instrument type)
#         mapping = {}
#         for d in data:
#             if d.get("segment") == "NSE_EQ" and d.get("instrument_type") == "EQ":
#                 symbol = d.get("trading_symbol")
#                 key = d.get("instrument_key")
#                 if symbol and key:
#                     mapping[symbol] = key
        
#         logger.info(f"[InstrumentResolver] Loaded {len(mapping)} NSE equity instruments successfully.")
        
#         # Merge with hardcoded SYMBOLS_MAPPING to ensure all curated symbols exist
#         for sym, key in Config.SYMBOLS_MAPPING.items():
#             if sym not in mapping:
#                 mapping[sym] = key
                
#         _symbols_map = mapping
#         return _symbols_map
#     except Exception as e:
#         logger.error(f"[InstrumentResolver] Error loading cached instruments: {e}. Falling back to Config.SYMBOLS_MAPPING.")
#         return dict(Config.SYMBOLS_MAPPING)

# def get_instrument_key(symbol: str) -> str:
#     """
#     Get the instrument key (token) for a given stock symbol.
#     Returns None if the symbol cannot be resolved.
#     """
#     mapping = load_instruments()
#     return mapping.get(symbol.upper())
