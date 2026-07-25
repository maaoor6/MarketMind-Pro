"""Structured logging configuration using structlog."""

import logging
import sys

import structlog

from src.utils.config import settings
from src.utils.redact import redact_processor


def configure_logging() -> None:
    """Configure structlog with appropriate processors for the environment."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="ISO"),
        # Scrub secrets from every event before it reaches a renderer/sink.
        redact_processor,
    ]

    if settings.app_env == "production":
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
        structlog.processors.JSONRenderer()
    else:
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
        structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging to capture third-party logs
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # httpx/httpcore log every request line at INFO ("HTTP Request: GET <url>").
    # That URL can carry a secret in its query/path (Google ?key=, FRED
    # ?api_key=, ExchangeRate /v6/<key>/), so raise their level to WARNING to
    # keep API keys out of the logs.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named structured logger."""
    return structlog.get_logger(name)


configure_logging()
