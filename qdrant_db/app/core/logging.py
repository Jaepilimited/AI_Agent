"""
로깅 초기화 - 콘솔 + 파일(db.log) 동시 출력
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_CONFIGURED = False


def setup_logging(log_file: str = "db.log", level: str = "INFO") -> logging.Logger:
    global _CONFIGURED

    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    logger = logging.getLogger("notion_rag")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _CONFIGURED:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # 파일 핸들러 (5MB 로테이션, 최대 3개)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger


logger = setup_logging()
