from __future__ import annotations

import logging
import os
from pathlib import Path


LOGGER_NAME = "mind_game"
DEFAULT_LOG_PATH = ".mind_game.log"


def configure_logging() -> Path:
    """Configure a process-wide file logger for runtime diagnostics."""
    log_path = Path(os.environ.get("MIND_GAME_LOG_PATH", DEFAULT_LOG_PATH)).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    resolved_path = log_path.resolve()
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_path:
            return resolved_path

    handler = logging.FileHandler(resolved_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ),
    )
    logger.addHandler(handler)
    logger.info("diagnostic logging configured path=%s", resolved_path)
    return resolved_path


def get_logger(name: str) -> logging.Logger:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
