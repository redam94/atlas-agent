"""Tests for atlas_core.logging."""

import json
import logging

import structlog

from atlas_core.logging import configure_logging


def test_configure_logging_production_emits_json(capsys):
    configure_logging(environment="production", log_level="INFO")
    log = structlog.get_logger("atlas.test")
    log.info("hello", key="value", number=42)

    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    parsed = json.loads(line)

    assert parsed["event"] == "hello"
    assert parsed["key"] == "value"
    assert parsed["number"] == 42
    assert parsed["level"] == "info"


def test_configure_logging_development_is_human_readable(capsys):
    configure_logging(environment="development", log_level="INFO")
    log = structlog.get_logger("atlas.test")
    log.info("hello", key="value")

    captured = capsys.readouterr()
    out = captured.out
    # Pretty (ConsoleRenderer) output is NOT valid JSON; just check the message rendered.
    assert "hello" in out
    assert "key" in out
    assert "value" in out


def test_configure_logging_respects_level(capsys):
    configure_logging(environment="production", log_level="WARNING")
    log = structlog.get_logger("atlas.test")
    log.info("should-not-appear")
    log.warning("should-appear")

    captured = capsys.readouterr()
    assert "should-not-appear" not in captured.out
    assert "should-appear" in captured.out


def test_configure_logging_is_idempotent():
    """Calling twice must not duplicate handlers or raise."""
    configure_logging(environment="development", log_level="INFO")
    configure_logging(environment="development", log_level="DEBUG")
    # If we got here without exceptions, OK.
    assert logging.getLogger().level == logging.DEBUG
