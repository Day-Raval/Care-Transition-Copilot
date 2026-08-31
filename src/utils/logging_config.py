"""
Shared logging setup for the pipeline. Every module that needs logging
calls setup_logging() once (idempotent — safe to call from multiple
entry points) and then uses logging.getLogger(__name__) as normal.

Console gets INFO and above (readable progress while a script runs).
logs/pipeline.log gets DEBUG and above (full detail for after-the-fact
debugging — e.g. tracing exactly which bundle a failure came from).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup_logging(log_dir: str = "logs", log_file: str = "pipeline.log", level: int = logging.DEBUG) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, log_file), maxBytes=5_000_000, backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _CONFIGURED = True