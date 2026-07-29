"""plan_sol step 4: AUTO_BI_PROFILE validation matrix + compose loopback binds."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auto_bi.config import Settings
from auto_bi.deployment_profile import (
    WEAK_SECRETS,
    is_loopback_host,
    is_weak_secret,
    normalize_profile,
    validate_deployment_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def _settings(**kwargs) -> Settings:
    """Settings without reading the developer .env."""
    return Settings(_env_file=None, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_normalize_profile_defaults_and_unknown() -> None:
    assert normalize_profile(None) == "local"
    assert normalize_profile("") == "local"
    assert normalize_profile("PRODUCTION") == "production"
    assert normalize_profile("nope") == "local"


def test_weak_secret_markers() -> None:
    assert is_weak_secret("")
    assert is_weak_secret("change_me")
    assert is_weak_secret("Admin_Local_Only")
    assert not is_weak_secret("correct-horse-battery-staple")
    assert "change_me" in WEAK_SECRETS


def test_loopback_hosts() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")


# ---------------------------------------------------------------------------
# local — never hard-fails
# ---------------------------------------------------------------------------


def test_local_profile_accepts_defaults() -> None:
    s = _settings()
    assert s.profile == "local"
    result = validate_deployment_profile(s, bind_host="127.0.0.1")
    assert result.ok
    assert result.profile == "local"
    assert result.errors == ()


def test_local_profile_warns_on_samples() -> None:
    s = _settings(send_samples=True)
    result = validate_deployment_profile(s)
    assert result.ok
    assert any("SEND_SAMPLES" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def test_demo_auto_only_ok() -> None:
    s = _settings(profile="demo", demo_auto_only=True)
    result = validate_deployment_profile(s, bind_host="127.0.0.1")
    assert result.ok, result.errors


def test_demo_auto_only_rejects_samples() -> None:
    s = _settings(profile="demo", demo_auto_only=True, send_samples=True)
    result = validate_deployment_profile(s)
    assert not result.ok
    assert any("SEND_SAMPLES" in e for e in result.errors)


def test_demo_text_requires_llm_ready_and_quota() -> None:
    s = _settings(profile="demo", demo_auto_only=False, require_llm_ready=False)
    result = validate_deployment_profile(s)
    assert not result.ok
    joined = " ".join(result.errors)
    assert "REQUIRE_LLM_READY" in joined
    assert "SESSION_RATE" in joined or "WORK_RATE" in joined or "LLM_BUDGET" in joined


def test_demo_text_ok_with_gates() -> None:
    s = _settings(
        profile="demo",
        demo_auto_only=False,
        require_llm_ready=True,
        session_rate_enabled=True,
    )
    result = validate_deployment_profile(s, bind_host="0.0.0.0")
    # non-loopback without auth/demo_auto/allow_insecure → error
    assert not result.ok
    assert any("non-loopback" in e for e in result.errors)

    s2 = _settings(
        profile="demo",
        demo_auto_only=False,
        require_llm_ready=True,
        work_rate_enabled=True,
        allow_insecure_remote=True,
    )
    result2 = validate_deployment_profile(s2, bind_host="0.0.0.0")
    assert result2.ok, result2.errors


def test_demo_text_llm_budget_counts_as_limit() -> None:
    s = _settings(
        profile="demo",
        demo_auto_only=False,
        require_llm_ready=True,
        llm_budget_enabled=True,
        llm_budget_day_max_calls=100,
    )
    result = validate_deployment_profile(s, bind_host="127.0.0.1")
    assert result.ok, result.errors


# ---------------------------------------------------------------------------
# production — matrix of required invariants
# ---------------------------------------------------------------------------


def _production_ok(**overrides) -> Settings:
    base = dict(
        profile="production",
        auth_enabled=True,
        admin_password="prod-admin-secret-9f3a",
        auth_cookie_secure=True,
        bi_connection_strict=True,
        send_samples=False,
        allow_insecure_remote=False,
        ch_password="prod-ch-secret-9f3a",
        superset_password="prod-ss-secret-9f3a",
        work_rate_enabled=True,
        retention_enabled=True,
        metrics_enabled=True,
        demo_auto_only=False,
    )
    base.update(overrides)
    return _settings(**base)


def test_production_happy_path() -> None:
    result = validate_deployment_profile(_production_ok(), bind_host="127.0.0.1")
    assert result.ok, result.errors
    assert result.warnings == ()


def test_production_warns_when_metrics_off() -> None:
    result = validate_deployment_profile(_production_ok(metrics_enabled=False))
    assert result.ok, result.errors
    assert any("METRICS" in w for w in result.warnings)


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"auth_enabled": False}, "AUTH_ENABLED"),
        ({"admin_password": "change_me"}, "ADMIN_PASSWORD"),
        ({"admin_password": ""}, "ADMIN_PASSWORD"),
        ({"auth_cookie_secure": None}, "AUTH_COOKIE_SECURE"),
        ({"auth_cookie_secure": False}, "AUTH_COOKIE_SECURE"),
        ({"bi_connection_strict": False}, "BI_CONNECTION_STRICT"),
        ({"send_samples": True}, "SEND_SAMPLES"),
        ({"allow_insecure_remote": True}, "ALLOW_INSECURE_REMOTE"),
        ({"demo_auto_only": True}, "DEMO_AUTO_ONLY"),
        ({"ch_password": "change_me"}, "CH_PASSWORD"),
        ({"superset_password": ""}, "SUPERSET_PASSWORD"),
        (
            {
                "work_rate_enabled": False,
                "session_rate_enabled": False,
                "llm_budget_enabled": False,
            },
            "resource limits",
        ),
        ({"retention_enabled": False}, "RETENTION_ENABLED"),
    ],
)
def test_production_rejects_insecure_combinations(override: dict, needle: str) -> None:
    result = validate_deployment_profile(_production_ok(**override))
    assert not result.ok, f"expected failure for {override}"
    assert any(needle in e for e in result.errors), result.errors


def test_production_accepts_users_file_instead_of_admin_password() -> None:
    s = _production_ok(admin_password="", auth_users_file="/etc/auto_bi/users.yaml")
    result = validate_deployment_profile(s)
    assert result.ok, result.errors


def test_production_accepts_llm_budget_instead_of_work_rate() -> None:
    s = _production_ok(
        work_rate_enabled=False,
        llm_budget_enabled=True,
        llm_budget_day_max_cost_usd=5.0,
    )
    result = validate_deployment_profile(s)
    assert result.ok, result.errors


def test_production_format_message_lists_errors() -> None:
    result = validate_deployment_profile(_settings(profile="production"))
    assert not result.ok
    msg = result.format_message()
    assert "AUTO_BI_PROFILE=production" in msg
    assert "AUTH_ENABLED" in msg


# ---------------------------------------------------------------------------
# Compose: default loopback; publish override is all-interfaces
# ---------------------------------------------------------------------------


def _port_specs(service: dict) -> list[str]:
    ports = service.get("ports") or []
    out: list[str] = []
    for p in ports:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            # long syntax
            host_ip = p.get("host_ip") or p.get("published_ip") or ""
            published = p.get("published")
            target = p.get("target")
            out.append(f"{host_ip}:{published}:{target}" if host_ip else f"{published}:{target}")
        else:
            out.append(str(p))
    return out


def test_compose_default_binds_loopback_only() -> None:
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in ("clickhouse", "superset"):
        specs = _port_specs(data["services"][name])
        assert specs, f"{name} has no ports"
        for spec in specs:
            assert spec.startswith("127.0.0.1:"), (
                f"{name} port {spec!r} must bind loopback in default compose "
                "(plan_sol step 4 / audit P0-3)"
            )


def test_compose_publish_override_opens_all_interfaces_and_requires_secrets() -> None:
    path = ROOT / "docker-compose.publish.yml"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    for name, expected_port in (("clickhouse", "8123:8123"), ("superset", "8088:8088")):
        specs = _port_specs(data["services"][name])
        assert expected_port in specs, specs
        # no loopback in the override — that is the point of the publish file
        assert not any(s.startswith("127.0.0.1:") for s in specs)
    # required-env markers so weak defaults cannot ship on LAN
    assert "CH_ADMIN_PASSWORD:?" in text or "CH_ADMIN_PASSWORD:?set" in text
    assert "SUPERSET_SECRET_KEY:?" in text or "SUPERSET_SECRET_KEY:?set" in text
    assert "AUTO_BI_CH_PASSWORD:?" in text or "AUTO_BI_CH_PASSWORD:?set" in text
    assert "AUTO_BI_SUPERSET_PASSWORD:?" in text or "AUTO_BI_SUPERSET_PASSWORD:?set" in text


def test_settings_profile_env_name() -> None:
    # every field maps to AUTO_BI_<FIELD>; profile → AUTO_BI_PROFILE
    assert "profile" in Settings.model_fields
    s = Settings(_env_file=None)
    assert s.profile == "local"
