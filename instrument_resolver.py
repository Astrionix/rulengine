import logging
from config import Config

logger = logging.getLogger(__name__)

def get_instrument_key(symbol: str) -> str:
    """
    Get the instrument key (token) for a given stock symbol.
    Falls back to Config.SYMBOLS_MAPPING.
    """
    return Config.SYMBOLS_MAPPING.get(symbol.upper())
