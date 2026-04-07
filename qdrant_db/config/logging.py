import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .settings import settings


_CONFIGURED = False


def setup_logging() -> Path:
    """Configure root logging once for console and file output."""
    global _CONFIGURED

    log_path = Path(settings.log_file)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    if _CONFIGURED:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    _CONFIGURED = True
    root_logger.info("Logging initialized: %s", log_path)
    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
