import logging
import sys
from logging.handlers import RotatingFileHandler

LOG_FILE = "cron_debug.log"

logger = logging.getLogger("cron")
logger.setLevel(logging.INFO)

# File logger (rotates at 5MB)
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=3
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
)

# Stdout logger (Render console)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
)

logger.addHandler(file_handler)
logger.addHandler(stdout_handler)
logger.propagate = False
