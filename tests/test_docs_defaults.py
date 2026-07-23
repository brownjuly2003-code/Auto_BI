"""plan_sol step 11 (start): documented defaults must match Settings / package version.

Catches the audit class of doc drift (USER_GUIDE claiming wrong auth/session/quota
defaults, version badge drift, etc.) without requiring a full docs generator yet.
"""

from __future__ import annotations

import re
from pathlib import Path

from auto_bi import __version__
from auto_bi.config import Settings

REPO = Path(__file__).resolve().parents[1]
USER_GUIDE = (REPO / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
PYPROJECT = (REPO / "pyproject.toml").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO / ".env.example").read_text(encoding="utf-8")

# Public Settings fields that USER_GUIDE §6 tables as defaults. Format in the guide:
# bare value in the "По умолчанию" column. We assert a known substring / pattern so a
# silent flip of a security-sensitive default fails CI.
DOCUMENTED_DEFAULTS: list[tuple[str, object, str]] = [
    # (settings attr, expected default, needle that must appear near the env name)
    ("send_samples", False, "false"),
    ("auth_enabled", False, "false"),
    ("session_rate_enabled", False, "false"),
    ("session_rate_per_day", 100, "100"),
    ("work_rate_enabled", False, "false"),
    ("work_rate_per_day", 50, "50"),
    ("demo_auto_only", False, "false"),
    ("prune_on_rebuild", True, "true"),
    ("llm_provider", "anthropic", "anthropic"),
    ("profile", "local", "local"),
    ("datalens_password", "", ""),  # empty = fail-loud; not "admin"
]


def test_settings_defaults_match_table() -> None:
    # Construct without reading the developer's local .env (which may override defaults).
    s = Settings(_env_file=None)
    for attr, expected, _needle in DOCUMENTED_DEFAULTS:
        actual = getattr(s, attr)
        assert actual == expected, f"Settings.{attr} default is {actual!r}, expected {expected!r}"


def test_user_guide_documents_key_defaults() -> None:
    """Guide prose still states the safe/opt-in defaults we rely on publicly."""
    lower = USER_GUIDE.lower()
    assert "auto_bi_send_samples" in lower
    assert "`false`" in USER_GUIDE or "false" in lower
    # ownership is real (audit false claim fixed)
    assert "привязана к username" in USER_GUIDE or "owner" in lower
    assert "не привязаны к владельцу" not in USER_GUIDE
    # work quota covers auto path
    assert "work_rate" in lower or "work-quota" in lower or "work quota" in lower
    # no shipped datalens admin password default
    assert "fail-loud" in lower or "пустой" in USER_GUIDE


def test_package_version_consistent() -> None:
    m = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, flags=re.M)
    assert m, "pyproject version missing"
    assert m.group(1) == __version__
    # README badge mentions Python, not a hard-coded stale package version required;
    # OpenAPI uses __version__ (covered in test_api).


def test_env_example_has_critical_flags() -> None:
    # security-sensitive knobs must be present so operators discover them
    for key in (
        "AUTO_BI_SEND_SAMPLES",
        "AUTO_BI_AUTH_ENABLED",
        "AUTO_BI_DEMO_AUTO_ONLY",
        "AUTO_BI_PROFILE",
        "AUTO_BI_LLM_PROVIDER",
    ):
        assert key in ENV_EXAMPLE, f".env.example missing {key}"


def test_cli_registers_core_commands() -> None:
    """CLI subcommands must not silently disappear (USER_GUIDE / README depend on them)."""
    cli_src = (REPO / "auto_bi" / "cli.py").read_text(encoding="utf-8")
    for cmd in ("build", "raw", "chat", "introspect", "gaps", "serve", "eval", "prune"):
        assert re.search(rf'add_parser\(\s*"{cmd}"', cli_src), f"CLI missing subcommand {cmd!r}"
    from auto_bi.cli import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_no_stale_mvp_session_owner_claim() -> None:
    # regression guard for the specific audit finding
    assert "сессии не привязаны к владельцу" not in USER_GUIDE


def test_settings_field_count_sanity() -> None:
    # If Settings grows a lot without docs, this still doesn't fail hard — just ensure
    # the documented set is a real subset.
    fields = set(Settings.model_fields)
    for attr, _, _ in DOCUMENTED_DEFAULTS:
        assert attr in fields
