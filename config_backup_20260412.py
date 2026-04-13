import os
from dotenv import load_dotenv
load_dotenv()

COINBASE_API_KEY     = os.getenv("COINBASE_API_KEY", "")
COINBASE_PRIVATE_KEY = os.getenv("COINBASE_PRIVATE_KEY", "")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
ALPACA_API_KEY       = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY    = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL      = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

TRADING_MODE         = os.getenv("TRADING_MODE", "paper").lower()
IS_LIVE              = TRADING_MODE == "live"
MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT", 0.05))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", 0.03))
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", 0.06))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.05))
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE", 0.65))
SCAN_INTERVAL        = int(os.getenv("SCAN_INTERVAL_SECONDS", 300))
PAPER_BALANCE        = float(os.getenv("PAPER_BALANCE", 10000))
KELLY_FRACTION       = 0.25
TOP_PAIRS_COUNT      = 20

CB_BASE_URL          = "https://api.coinbase.com"
CB_API_PATH          = "/api/v3/brokerage"
CANDLE_GRANULARITY   = "ONE_HOUR"
CANDLE_LIMIT         = 100
LOG_FILE             = "tradeiq.log"
TRADES_CSV           = "trades.csv"

CRYPTO_PAIRS = []

STOCK_SYMBOLS = [
    "MSFT", "NVDA", "TSLA", "AMZN",
    "META", "GOOGL", "AMD",  
    "SPY",  "QQQ",  "SOFI", "HOOD", 
    "INTC", "NFLX", "DIS",  "UBER", "BABA",
]

def validate_config():
    errors = []
    if not COINBASE_API_KEY or "YOUR_ORG_ID" in COINBASE_API_KEY:
        errors.append("COINBASE_API_KEY is not set in .env")
    if not COINBASE_PRIVATE_KEY or "YOUR_PRIVATE_KEY" in COINBASE_PRIVATE_KEY:
        errors.append("COINBASE_PRIVATE_KEY is not set in .env")
    if not ANTHROPIC_API_KEY or "YOUR_KEY" in ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set in .env")
    if errors:
        raise EnvironmentError(
            "\n\nMissing configuration:\n" +
            "\n".join(f"  x {e}" for e in errors) +
            "\n\nCopy .env.example to .env and fill in your keys.\n"
        )
    return True

# Kalshi
KALSHI_API_KEY  = os.getenv("KALSHI_API_KEY", "")
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2")
KALSHI_MARKETS  = 20  # number of top markets to scan
KALSHI_PRIVATE_KEY = os.getenv("KALSHI_PRIVATE_KEY", "")
TWILIO_SID   = os.getenv("TWILIO_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "")
TWILIO_FROM  = os.getenv("TWILIO_FROM", "")
TWILIO_TO    = os.getenv("TWILIO_TO", "")
MAX_OPEN_POSITIONS = int(os.getenv('MAX_OPEN_POSITIONS', 4))
STOCK_TP_PCT = float(os.getenv("STOCK_TP_PCT", 0.06))
STOCK_SL_PCT = float(os.getenv("STOCK_SL_PCT", 0.03))
