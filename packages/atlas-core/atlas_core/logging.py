"""Structured logging configuration for ATLAS.

Call ``configure_logging(environment, log_level)`` once at application startup.
Production produces JSON lines suitable for log aggregation; development
produces human-readable colorized output.
"""

import logging
import sys

import structlog


def configure_logging(environment: str, log_level: str = "INFO") -> None:
    """Configure both stdlib logging and structlog.

    Idempotent — safe to call multiple times.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Reset stdlib root handlers (idempotency)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if environment == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
