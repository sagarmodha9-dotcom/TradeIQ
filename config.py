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
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", 0.04))
STOCK_SL_PCT         = float(os.getenv("STOCK_SL_PCT", 0.03))
STOCK_TP_PCT         = float(os.getenv("STOCK_TP_PCT", 0.06))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.05))
MAX_OPEN_POSITIONS   = int(os.getenv("MAX_OPEN_POSITIONS", 4))
MAX_DAILY_LOSS_PCT   = float(os.getenv("MAX_DAILY_LOSS_PCT", 3.0))
MAX_OPEN_OPTIONS     = int(os.getenv("MAX_OPEN_OPTIONS",    0))  # DISABLED 5/21 - TT funds moved to Alpaca
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE", 0.72))
MIN_INDICATOR_AGREE  = int(os.getenv("MIN_INDICATOR_AGREE", 2))
MAX_VIX              = float(os.getenv("MAX_VIX", 30.0))
NO_TRADE_OPEN_MINS   = int(os.getenv("NO_TRADE_OPEN_MINS", 15))
NO_TRADE_CLOSE_MINS  = int(os.getenv("NO_TRADE_CLOSE_MINS", 15))
KELLY_FRACTION       = 0.25
SCAN_INTERVAL        = int(os.getenv("SCAN_INTERVAL_SECONDS", 60))
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

def get_daily_loss_limit_usd():
    """Return daily loss limit in USD based on account balance."""
    balance = LIVE_ACCOUNT_BALANCE if IS_LIVE else PAPER_BALANCE
    return abs(balance * DAILY_LOSS_LIMIT_PCT)

# Dynamic TP/SL by symbol volatility tier
# High vol: TSLA, NVDA, COIN, AMD — wider TP to let winners run
# Mid vol: META, GOOGL, AMZN, MSFT — standard TP
# Low vol: SPY, QQQ, JPM, BAC — tighter TP for faster hits
SYMBOL_TP_PCT = {
    # High volatility — let run
    "TSLA": 0.08, "NVDA": 0.07, "COIN": 0.09, "AMD":  0.07,
    "HOOD": 0.09, "SOFI": 0.08, "PLTR": 0.07, "APP":  0.08,
    "SNAP": 0.09, "ROKU": 0.09, "SHOP": 0.07, "SQ":   0.07,
    "BABA": 0.07,
    # Mid volatility — standard
    "META": 0.06, "GOOGL": 0.06, "AMZN": 0.06, "MSFT": 0.05,
    "AAPL": 0.05, "NFLX":  0.06, "UBER": 0.06, "DIS":  0.06,
    "INTC": 0.06,
    # Low volatility — tight TP for more hits
    "SPY":  0.03, "QQQ":  0.03, "JPM":  0.04, "BAC":  0.04,
}

SYMBOL_SL_PCT = {
    # High volatility — wider SL to avoid shakeouts
    "TSLA": 0.04, "NVDA": 0.035, "COIN": 0.045, "AMD":  0.035,
    "HOOD": 0.04, "SOFI": 0.04,  "PLTR": 0.035, "APP":  0.04,
    "SNAP": 0.04, "ROKU": 0.04,  "SHOP": 0.035, "SQ":   0.035,
    "BABA": 0.035,
    # Mid volatility
    "META": 0.03, "GOOGL": 0.03, "AMZN": 0.03, "MSFT": 0.03,
    "AAPL": 0.03, "NFLX":  0.03, "UBER": 0.03, "DIS":  0.03,
    "INTC": 0.03,
    # Low volatility — tight SL
    "SPY":  0.015, "QQQ": 0.015, "JPM":  0.02, "BAC":  0.02,
}

def get_tp_pct(symbol):
    return SYMBOL_TP_PCT.get(symbol, STOCK_TP_PCT)

def get_sl_pct(symbol):
    return SYMBOL_SL_PCT.get(symbol, STOCK_SL_PCT)


# Options budget caps (added May 4 2026)
OPTIONS_BUDGET_PCT_OF_TT  = float(os.getenv("OPTIONS_BUDGET_PCT_OF_TT", 0.30))   # 30% of TT total per regular options trade
OPTIONS_BUDGET_HARD_CAP   = int(os.getenv("OPTIONS_BUDGET_HARD_CAP", 600))       # ceiling for regular options
EARNINGS_PLAY_BUDGET_CAP  = int(os.getenv("EARNINGS_PLAY_BUDGET_CAP", 300))      # ceiling for earnings plays (was 1000)

# Cheap underlyings for options — symbols where ATM weekly calls fit in <$200 budget (added 5/21)
CHEAP_OPTIONS_UNIVERSE = os.getenv("CHEAP_OPTIONS_UNIVERSE", "SOFI,INTC,PLTR,SNAP,F,BAC,HOOD,NU,T,UBER,AAL,CCL,RIVN,LCID,PFE,WBD,GRAB").split(",")
