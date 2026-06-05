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
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", 0.045))
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", 0.04))
STOCK_SL_PCT         = float(os.getenv("STOCK_SL_PCT", 0.045))
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
    "META", "GOOGL", "AMD",  "SPY",
    "QQQ",  "HOOD", "INTC", "NFLX",
    "UBER", "AAPL", "JPM",  "BAC",
    "COIN", "PLTR", "APP",  "ROKU",
    "MU",   "AVGO", "SMCI", "CRWD",
    "MRVL", "AMAT", "DELL", "ORCL",
    "LRCX", "RDDT",
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
    # High volatility — let run (lowered 5/21 from 7-9% to 5-6%)
    "TSLA": 0.06, "NVDA": 0.05, "COIN": 0.06, "AMD":  0.05,
    "HOOD": 0.06, "SOFI": 0.06, "PLTR": 0.05, "APP":  0.06,
    "SNAP": 0.06, "ROKU": 0.06, "SHOP": 0.05, "SQ":   0.05,
    "BABA": 0.05,
    # Mid volatility (lowered from 5-6% to 3.5-4%)
    "META": 0.04, "GOOGL": 0.04, "AMZN": 0.04, "MSFT": 0.035,
    "AAPL": 0.035,"NFLX":  0.04, "UBER": 0.04, "DIS":  0.04,
    "INTC": 0.04,
    # Low volatility (lowered from 3-4% to 2-2.5%)
    "SPY":  0.02, "QQQ":  0.02, "JPM":  0.025, "BAC":  0.025,
}

SYMBOL_SL_PCT = {
    # High volatility — wider SL to avoid shakeouts (widened 5/21 from 3.5-4.5% to 5-6%)
    "TSLA": 0.055, "NVDA": 0.05, "COIN": 0.06, "AMD":  0.05,
    "HOOD": 0.055, "SOFI": 0.055,"PLTR": 0.05, "APP":  0.055,
    "SNAP": 0.055, "ROKU": 0.055,"SHOP": 0.05, "SQ":   0.05,
    "BABA": 0.05,
    # Mid volatility (widened from 3% to 4.5%)
    "META": 0.045, "GOOGL": 0.045, "AMZN": 0.045, "MSFT": 0.045,
    "AAPL": 0.045, "NFLX":  0.045, "UBER": 0.045, "DIS":  0.045,
    "INTC": 0.045,
    # Low volatility (widened from 1.5-2% to 2.5-3%)
    "SPY":  0.025, "QQQ": 0.025, "JPM":  0.03, "BAC":  0.03,
}

def get_tp_pct(symbol):
    if DAY_TRADING_MODE:
        return STOCK_TP_PCT  # day trading: tight uniform TP, per-symbol dict ignored
    return SYMBOL_TP_PCT.get(symbol, STOCK_TP_PCT)

def get_sl_pct(symbol):
    if DAY_TRADING_MODE:
        return STOCK_SL_PCT  # day trading: tight uniform SL, per-symbol dict ignored
    return SYMBOL_SL_PCT.get(symbol, STOCK_SL_PCT)


# Options budget caps (added May 4 2026)
OPTIONS_BUDGET_PCT_OF_TT  = float(os.getenv("OPTIONS_BUDGET_PCT_OF_TT", 0.30))   # 30% of TT total per regular options trade
OPTIONS_BUDGET_HARD_CAP   = int(os.getenv("OPTIONS_BUDGET_HARD_CAP", 600))       # ceiling for regular options
EARNINGS_PLAY_BUDGET_CAP  = int(os.getenv("EARNINGS_PLAY_BUDGET_CAP", 300))      # ceiling for earnings plays (was 1000)

# Cheap underlyings for options — symbols where ATM weekly calls fit in <$200 budget (added 5/21)
CHEAP_OPTIONS_UNIVERSE = os.getenv("CHEAP_OPTIONS_UNIVERSE", "SOFI,INTC,PLTR,SNAP,F,BAC,HOOD,NU,T,UBER,AAL,CCL,RIVN,LCID,PFE,WBD,GRAB").split(",")


# Blacklisted symbols (added 5/21) — 0% win rate in last 45 trades
EXCLUDED_SYMBOLS = os.getenv("EXCLUDED_SYMBOLS", "META,UBER").split(",")


# ===== DAY TRADING MODE (June 3, 2026) =====
DAY_TRADING_MODE        = os.getenv("DAY_TRADING_MODE", "false").lower() == "true"
BAR_TIMEFRAME           = os.getenv("BAR_TIMEFRAME", "5Min") if DAY_TRADING_MODE else "1Hour"
POSITION_SIZE_PCT       = float(os.getenv("POSITION_SIZE_PCT", 0.35))
MAX_TRADES_PER_DAY      = int(os.getenv("MAX_TRADES_PER_DAY", 8))
DAILY_LOSS_LIMIT_USD    = float(os.getenv("DAILY_LOSS_LIMIT_USD", 100))
EQUITY_FLOOR            = float(os.getenv("EQUITY_FLOOR", 2100))
COOLDOWN_WIN_MIN        = int(os.getenv("COOLDOWN_WIN_MIN", 12))
COOLDOWN_LOSS_MIN       = int(os.getenv("COOLDOWN_LOSS_MIN", 35))
FORCE_CLOSE_HOUR_ET     = int(os.getenv("FORCE_CLOSE_HOUR_ET", 15))
FORCE_CLOSE_MINUTE_ET   = int(os.getenv("FORCE_CLOSE_MINUTE_ET", 50))
NO_ENTRY_BEFORE_ET      = os.getenv("NO_ENTRY_BEFORE_ET", "09:45")
SHORTS_ENABLED          = os.getenv("SHORTS_ENABLED", "false").lower() == "true"
SHORT_SL_PCT            = float(os.getenv("SHORT_SL_PCT", 0.005))
SHORT_TP_PCT            = float(os.getenv("SHORT_TP_PCT", 0.008))
SHORT_HTB_COOLDOWN_MIN  = int(os.getenv("SHORT_HTB_COOLDOWN_MIN", 120))
CONSEC_LOSS_BREAKER_COUNT      = int(os.getenv("CONSEC_LOSS_BREAKER_COUNT", 3))
CONSEC_LOSS_BREAKER_WINDOW_MIN = int(os.getenv("CONSEC_LOSS_BREAKER_WINDOW_MIN", 30))
CONSEC_LOSS_BREAKER_HALT_MIN   = int(os.getenv("CONSEC_LOSS_BREAKER_HALT_MIN", 60))
