"""Local rotating logging for API, CLI, and review pipelines."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(settings=None) -> Path:
    """Configure one console and one local rotating file handler.

    Relative log paths resolve from the codebase root, independent of the
    current working directory. Prompts, evidence values, and secrets should
    never be included in log messages.
    """
    code_root = Path(__file__).resolve().parents[1]
    configured_path = Path(getattr(settings, "spectra_log_file", "logs/spectra.log"))
    log_path = configured_path if configured_path.is_absolute() else code_root / configured_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level_name = str(getattr(settings, "spectra_log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = next(
        (handler for handler in root.handlers
         if getattr(handler, "_spectra_file_handler", False)), None)
    file_handler_created = file_handler is None
    if file_handler is None:
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5,
            encoding="utf-8", delay=False)
        file_handler._spectra_file_handler = True
        root.addHandler(file_handler)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = next(
        (handler for handler in root.handlers
         if getattr(handler, "_spectra_console_handler", False)), None)
    if console_handler is None:
        console_handler = logging.StreamHandler()
        console_handler._spectra_console_handler = True
        root.addHandler(console_handler)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    if file_handler_created:
        logging.getLogger("spectra").info(
            "event=logging_configured file=%s rotation_bytes=%d backups=%d",
            log_path, 10 * 1024 * 1024, 5)

    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    return log_path
