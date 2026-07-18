"""C-2: misspelled AUTO_BI_* env vars must be surfaced, not silently ignored.

Settings uses extra="ignore", so AUTO_BI_AUTH_ENABLE=true (missing the D) leaves
auth OFF with zero trace. `serve` calls warn_unknown_env_settings at startup."""

import logging

from auto_bi.config import Settings, unknown_env_settings, warn_unknown_env_settings


def test_typo_is_detected() -> None:
    env = {"AUTO_BI_AUTH_ENABLE": "true", "AUTO_BI_CH_HOST": "localhost", "PATH": "/x"}
    assert unknown_env_settings(env) == ["AUTO_BI_AUTH_ENABLE"]


def test_known_vars_any_case_are_not_flagged() -> None:
    env = {"AUTO_BI_AUTH_ENABLED": "true", "auto_bi_ch_port": "9000"}
    assert unknown_env_settings(env) == []


def test_every_settings_field_env_name_is_known() -> None:
    env = {f"AUTO_BI_{name.upper()}": "x" for name in Settings.model_fields}
    assert unknown_env_settings(env) == []


def test_non_auto_bi_vars_are_ignored() -> None:
    assert unknown_env_settings({"HOME": "/h", "AUTOBI_TYPO": "1"}) == []


def test_auth_enable_typo_warns_in_log(caplog) -> None:
    log = logging.getLogger("auto_bi.test_c2")
    with caplog.at_level(logging.WARNING, logger=log.name):
        flagged = warn_unknown_env_settings(log, {"AUTO_BI_AUTH_ENABLE": "true"})
    assert flagged == ["AUTO_BI_AUTH_ENABLE"]
    assert any(
        "AUTO_BI_AUTH_ENABLE" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )
