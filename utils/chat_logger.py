"""Dedicated chatbot logger.

Every chat interaction is logged to logs/chat.log (rotating, 1 MB x 5 files)
AND to stdout via the app's standard format. Logged events:

  - the user's question (who asked, truncated text)
  - every SQL query Claude attempts, and whether the guardrails passed it
  - guardrail rejections with the reason
  - tool results (row counts), token usage, final answer length
  - errors (API auth, rate limits, DB failures)
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)s | chatbot | %(message)s"

chat_logger = logging.getLogger("chatbot")
chat_logger.setLevel(logging.INFO)

if not chat_logger.handlers:  # guard against double-adding on reload
    # Cloudflare Workers have no writable/persistent filesystem, so the rotating
    # file handler is best-effort: local dev gets logs/chat.log as before, and
    # a Worker sandbox (or anywhere file I/O is unavailable) silently falls back
    # to stdout only — chat events still show up via propagation either way.
    try:
        _log_dir = Path(__file__).resolve().parent.parent / "logs"
        _log_dir.mkdir(exist_ok=True)
        _file = RotatingFileHandler(
            _log_dir / "chat.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        _file.setFormatter(logging.Formatter(_FORMAT))
        chat_logger.addHandler(_file)
    except OSError:
        pass
    # Also propagate to root (stdout) so chat events appear in the app log.
    chat_logger.propagate = True


def trunc(text: str, limit: int = 300) -> str:
    """Truncate long text for log lines."""
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
