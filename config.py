import os
from dotenv import load_dotenv
load_dotenv()

ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
TWILIO_SID           = os.getenv("TWILIO_SID", "")
TWILIO_TOKEN         = os.getenv("TWILIO_TOKEN", "")
TWILIO_FROM          = os.getenv("TWILIO_FROM", "")
TWILIO_TO            = os.getenv("TWILIO_TO", "")

TRADING_MODE         = os.getenv("TRADING_MODE", "paper").lower()
IS_LIVE              = TRADING_MODE == "live"

PAPER_BALANCE        = float(os.getenv("PAPER_BALANCE", 14949))
LIVE_ACCOUNT_BALANCE = float(os.getenv("LIVE_ACCOUNT_BALANCE", 2000))

MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT", 0.05))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", 0.03))
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", 0.06))
STOCK_SL_PCT         = float(os.getenv("STOCK_SL_PCT", 0.03))
STOCK_TP_PCT         = float(os.getenv("STOCK_TP_PCT", 0.06))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.05))
MAX_OPEN_POSITIONS   = int(os.getenv("MAX_OPEN_POSITIONS", 4))
MAX_OPEN_OPTIONS     = int(os.getenv("MAX_OPEN_OPTIONS",    4))
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE", 0.72))
MIN_INDICATOR_AGREE  = int(os.getenv("MIN_INDICATOR_AGREE", 2))
MAX_VIX              = float(os.getenv("MAX_VIX", 30.0))
NO_TRADE_OPEN_MINS   = int(os.getenv("NO_TRADE_OPEN_MINS", 15))
NO_TRADE_CLOSE_MINS  = int(os.getenv("NO_TRADE_CLOSE_MINS", 15))
KELLY_FRACTION       = 0.25
SCAN_INTERVAL        = int(os.getenv("SCAN_INTERVAL_SECONDS", 300))
KILL_SWITCH          = os.getenv("KILL_SWITCH", "0") == "1"

# Tastytrade
TASTYTRADE_USERNAME  = os.getenv("TASTYTRADE_USERNAME", "")
TASTYTRADE_PASSWORD  = os.getenv("TASTYTRADE_PASSWORD", "")

# Alpaca
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

COINBASE_API_KEY     = os.getenv("COINBASE_API_KEY", "")
COINBASE_PRIVATE_KEY = os.getenv("COINBASE_PRIVATE_KEY", "")

TOP_PAIRS_COUNT      = 20
CB_BASE_URL          = "https://api.coinbase.com"
CB_API_PATH          = "/api/v3/brokerage"
CANDLE_GRANULARITY   = "ONE_HOUR"
CANDLE_LIMIT         = 100
LOG_FILE             = os.getenv("LOG_FILE", "tradeiq.log")
TRADES_CSV           = "trades.csv"
CRYPTO_PAIRS         = []

STOCK_SYMBOLS = [
    "MSFT", "NVDA", "TSLA", "AMZN",
    "META", "GOOGL", "AMD",
    "SPY",  "QQQ",  "SOFI", "HOOD",
    "INTC", "NFLX", "DIS",  "UBER", "BABA",
    "AAPL", "JPM",  "BAC",  "COIN", "PLTR",
    "APP",  "SNAP", "ROKU", "SHOP", "SQ",
]

def validate_config():
    """Validate config before bot starts."""
    errors = []
    if MIN_CONFIDENCE < 0.72:
        errors.append(f"MIN_CONFIDENCE={MIN_CONFIDENCE} below 0.72 — unsafe for live")
    if not ALPACA_API_KEY:
        errors.append("ALPACA_API_KEY not set")
    if not ALPACA_SECRET_KEY:
        errors.append("ALPACA_SECRET_KEY not set")
    if errors:
        raise EnvironmentError("\n\nConfig errors:\n" + "\n".join(f"  x {e}" for e in errors))
    return True

def get_account_balance():
    if not IS_LIVE:
        return PAPER_BALANCE
    return LIVE_ACCOUNT_BALANCE
