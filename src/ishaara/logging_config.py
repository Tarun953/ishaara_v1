"""structlog setup that emits JSON Cloud Logging understands."""

import logging
import sys
from typing import Any, TextIO

import structlog


def _add_severity(
    logger: Any, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Copy the log level into the ``severity`` field Cloud Logging reads."""
    event_dict["severity"] = str(event_dict.get("level", method_name)).upper()
    return event_dict


def configure_logging(log_level: str = "INFO", stream: TextIO | None = None) -> None:
    """Configure structlog to write key-value JSON events to stdout.

    Args:
        log_level: Minimum level name such as ``"INFO"`` or ``"DEBUG"``.
        stream: Where events are written; defaults to stdout (the CLI uses stderr).
    """
    output_stream = stream or sys.stdout
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=output_stream, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_severity,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=output_stream),
        cache_logger_on_first_use=True,
    )
