"""Validated deployment profiles (plan_sol step 4 / audit P0-3, P2-4).

``AUTO_BI_PROFILE=local|demo|production`` selects how strictly serve-time
settings are checked before the server binds a socket.

- **local** — developer defaults; no hard fail (compat with CLI/tests).
- **demo** — public/demo Space: auto-only *or* text-mode with quota + LLM gate.
- **production** — fail closed on weak secrets, missing auth, samples opt-in
  without care, and other combinations that are fine locally but unsafe in prod.

Validation is pure (no I/O) so unit tests cover the full allow/deny matrix
without starting uvicorn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from auto_bi.config import Settings

DeploymentProfile = Literal["local", "demo", "production"]
VALID_PROFILES: Final[frozenset[str]] = frozenset({"local", "demo", "production"})

# Well-known placeholders shipped in compose / .env.example. Production and
# the compose *publish* path must not start with these still set.
WEAK_SECRETS: Final[frozenset[str]] = frozenset(
    {
        "",
        "change_me",
        "changeme",
        "password",
        "secret",
        "admin",
        "admin_local_only",
        "dev_secret_local_only",
        "your_password_here",
        "replace_me",
    }
)

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class ProfileCheckResult:
    """Outcome of :func:`validate_deployment_profile`."""

    profile: DeploymentProfile
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def format_message(self) -> str:
        lines = [
            f"AUTO_BI_PROFILE={self.profile} rejected unsafe configuration:",
            *[f"  - {e}" for e in self.errors],
            "",
            "Fix the settings above, or use AUTO_BI_PROFILE=local for development.",
            "See docs/DEPLOYMENT.md § deployment profiles.",
        ]
        return "\n".join(lines)


def normalize_profile(raw: str | None) -> DeploymentProfile:
    """Map a settings/env value to a known profile; unknown → treat as local with care."""
    value = (raw or "local").strip().lower()
    if value in VALID_PROFILES:
        return value  # type: ignore[return-value]
    return "local"


def is_weak_secret(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() in WEAK_SECRETS


def is_loopback_host(host: str) -> bool:
    return host.strip().lower() in LOOPBACK_HOSTS


def _has_rate_or_budget(settings: Settings) -> bool:
    if settings.session_rate_enabled or settings.work_rate_enabled:
        return True
    if not settings.llm_budget_enabled:
        return False
    return any(
        (
            settings.llm_budget_session_max_calls > 0,
            settings.llm_budget_session_max_tokens > 0,
            settings.llm_budget_session_max_seconds > 0,
            settings.llm_budget_session_max_cost_usd > 0,
            settings.llm_budget_day_max_calls > 0,
            settings.llm_budget_day_max_tokens > 0,
            settings.llm_budget_day_max_seconds > 0,
            settings.llm_budget_day_max_cost_usd > 0,
        )
    )


def validate_deployment_profile(
    settings: Settings,
    *,
    bind_host: str | None = None,
) -> ProfileCheckResult:
    """Validate settings against the active deployment profile.

    ``bind_host`` is the address ``serve`` will bind (CLI ``--host``). Used for
    remote-bind and cookie checks; optional for pure settings tests.
    """
    profile = normalize_profile(getattr(settings, "profile", None))
    errors: list[str] = []
    warnings: list[str] = []

    if profile == "local":
        if settings.send_samples:
            warnings.append(
                "AUTO_BI_SEND_SAMPLES=true on local profile — DWH values may leave the process"
            )
        return ProfileCheckResult(profile=profile, errors=tuple(errors), warnings=tuple(warnings))

    if profile == "demo":
        return _validate_demo(settings, bind_host=bind_host)

    # production
    return _validate_production(settings, bind_host=bind_host)


def _validate_demo(
    settings: Settings,
    *,
    bind_host: str | None,
) -> ProfileCheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    if settings.demo_auto_only:
        # Auto-only is the safe public path: no LLM, work rate forced in create_app.
        if settings.send_samples:
            errors.append(
                "demo auto-only must keep AUTO_BI_SEND_SAMPLES=false "
                "(no reason to export DWH values without an LLM path)"
            )
    else:
        # Text-enabled demo: must not come up without LLM readiness + quotas.
        if not settings.require_llm_ready:
            errors.append(
                "text-enabled demo requires AUTO_BI_REQUIRE_LLM_READY=true "
                "(or set AUTO_BI_DEMO_AUTO_ONLY=true)"
            )
        if not _has_rate_or_budget(settings):
            errors.append(
                "text-enabled demo requires at least one of "
                "AUTO_BI_SESSION_RATE_ENABLED, AUTO_BI_WORK_RATE_ENABLED, "
                "or AUTO_BI_LLM_BUDGET_ENABLED with a non-zero limit"
            )
        if settings.send_samples:
            warnings.append(
                "AUTO_BI_SEND_SAMPLES=true on demo — only public/internal classes leave the process"
            )

    if (
        bind_host is not None
        and not is_loopback_host(bind_host)
        and not (settings.auth_enabled or settings.demo_auto_only or settings.allow_insecure_remote)
    ):
        errors.append(
            f"non-loopback bind {bind_host!r} requires auth, demo_auto_only, "
            "or AUTO_BI_ALLOW_INSECURE_REMOTE=true"
        )

    return ProfileCheckResult(profile="demo", errors=tuple(errors), warnings=tuple(warnings))


def _validate_production(
    settings: Settings,
    *,
    bind_host: str | None,
) -> ProfileCheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not settings.auth_enabled:
        errors.append("production requires AUTO_BI_AUTH_ENABLED=true")

    if settings.auth_enabled:
        has_users_file = bool(settings.auth_users_file.strip())
        if not has_users_file and is_weak_secret(settings.admin_password):
            errors.append(
                "production auth requires AUTO_BI_AUTH_USERS_FILE or a non-default "
                "AUTO_BI_ADMIN_PASSWORD"
            )
        if settings.auth_cookie_secure is not True:
            errors.append(
                "production requires AUTO_BI_AUTH_COOKIE_SECURE=true "
                "(do not rely on loopback auto-heuristic behind a reverse proxy)"
            )

    if not settings.bi_connection_strict:
        errors.append(
            "production requires AUTO_BI_BI_CONNECTION_STRICT=true "
            "(refuse stale BI connection fingerprints)"
        )

    if settings.send_samples:
        errors.append(
            "production requires AUTO_BI_SEND_SAMPLES=false "
            "(opt-in samples are for controlled non-prod or explicitly accepted residual risk)"
        )

    if settings.allow_insecure_remote:
        errors.append(
            "production forbids AUTO_BI_ALLOW_INSECURE_REMOTE=true — use auth + reverse proxy"
        )

    if settings.demo_auto_only:
        errors.append(
            "production must not set AUTO_BI_DEMO_AUTO_ONLY=true "
            "(use AUTO_BI_PROFILE=demo for the public demo)"
        )

    # DWH / BI application secrets that production always uses on the v1 path.
    for name, value in (
        ("AUTO_BI_CH_PASSWORD", settings.ch_password),
        ("AUTO_BI_SUPERSET_PASSWORD", settings.superset_password),
    ):
        if is_weak_secret(value):
            errors.append(f"{name} is empty or a known default placeholder")

    if not _has_rate_or_budget(settings):
        errors.append(
            "production requires resource limits: enable AUTO_BI_WORK_RATE_ENABLED "
            "and/or AUTO_BI_SESSION_RATE_ENABLED and/or AUTO_BI_LLM_BUDGET_ENABLED "
            "with at least one non-zero limit"
        )

    if not settings.retention_enabled:
        errors.append(
            "production requires AUTO_BI_RETENTION_ENABLED=true "
            "(telemetry tables must not grow unbounded)"
        )

    if not settings.metrics_enabled:
        warnings.append(
            "AUTO_BI_METRICS_ENABLED=false — production usually enables /api/v1/metrics "
            "for operators (admin-only when auth is on)"
        )

    # Production may bind 0.0.0.0 behind a reverse proxy on a private network,
    # but only with auth (already required) and never with allow_insecure_remote.
    if bind_host is not None and not is_loopback_host(bind_host) and not settings.auth_enabled:
        errors.append(
            f"production non-loopback bind {bind_host!r} requires AUTO_BI_AUTH_ENABLED=true"
        )

    return ProfileCheckResult(profile="production", errors=tuple(errors), warnings=tuple(warnings))
