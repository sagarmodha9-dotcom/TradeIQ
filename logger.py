import logging
import sys
from colorama import Fore, Style, init
init(autoreset=True)

COLOR_MAP = {
    "DEBUG": Fore.CYAN,
    "INFO": Fore.WHITE,
    "WARNING": Fore.YELLOW,
    "ERROR": Fore.RED,
    "CRITICAL": Fore.MAGENTA,
}

class ColorFormatter(logging.Formatter):
    FMT = "%(asctime)s  %(levelname)-8s  %(message)s"
    DATE = "%H:%M:%S"
    def format(self, record):
        color = COLOR_MAP.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return logging.Formatter(self.FMT, datefmt=self.DATE).format(record)

def _setup_logger():
    import config as cfg
    logger = logging.getLogger("tradeiq")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColorFormatter())
    logger.addHandler(ch)
    fh = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    return logger

log = _setup_logger()
