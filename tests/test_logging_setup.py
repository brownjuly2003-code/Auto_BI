"""configure_logging (O-3): level + text/JSON formatting for `auto_bi serve`.

Root-logger state is global, so every test restores it — otherwise a `json`-format run
here would leak into unrelated tests that use `caplog`/root handlers later in the suite.
"""

import json
import logging

import pytest

from auto_bi.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_configure_logging_sets_level_and_single_handler() -> None:
    configure_logging("DEBUG", "text")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO", "text")
    configure_logging("WARNING", "json")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1  # re-configuring never stacks handlers


def test_json_format_emits_one_parseable_object_per_record(capsys) -> None:
    configure_logging("INFO", "json")
    logging.getLogger("auto_bi.test").info("hello %s", "world")
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello world"
    assert payload["logger"] == "auto_bi.test"


def test_text_format_is_human_readable_not_json(capsys) -> None:
    configure_logging("INFO", "text")
    logging.getLogger("auto_bi.test").info("plain line")
    out = capsys.readouterr().out.strip()
    assert "plain line" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_level_filters_below_threshold(capsys) -> None:
    configure_logging("WARNING", "text")
    logging.getLogger("auto_bi.test").info("should not appear")
    assert capsys.readouterr().out == ""
