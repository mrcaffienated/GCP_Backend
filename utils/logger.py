import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
