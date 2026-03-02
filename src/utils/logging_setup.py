"""Run-scoped logging setup."""

import logging
import sys
from datetime import date

from src.storage.local_store import logs_dir


def setup_logging(run_date: str | None = None) -> logging.Logger:
    rd = run_date or date.today().isoformat()
    log_file = logs_dir() / f"run_{rd}.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return root
