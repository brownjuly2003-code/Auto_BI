"""plan_sol step 11: docs-as-code ratchets (env ref, links, eval counts)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

from auto_bi.config import Settings
from auto_bi.eval.cases import (
    ADVISOR_CASES,
    GOLDEN_CASES,
    GP_ADVISOR_CASES,
    GP_GOLDEN_CASES,
)

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
ENV_REF = DOCS / "ENV_REFERENCE.md"
CURRENT_STATE = DOCS / "CURRENT_STATE.md"
ARCHITECTURE = DOCS / "ARCHITECTURE.md"
ARCHITECTURE_HISTORY = DOCS / "ARCHITECTURE_HISTORY.md"
USER_GUIDE = DOCS / "USER_GUIDE.md"
README = REPO / "README.md"
ENV_EXAMPLE = REPO / ".env.example"
FIXTURES_DIR = REPO / "tests" / "fixtures" / "golden_llm"
GEN_SCRIPT = REPO / "scripts" / "generate_env_reference.py"
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
SLO = DOCS / "operations" / "SLO.md"

# Markdown files that participate in the public doc graph (internal links checked).
PUBLIC_MD = [
    REPO / "README.md",
    REPO / "CHANGELOG.md",
    REPO / "CONTRIBUTING.md",
    REPO / "SECURITY.md",
    REPO / "plan.md",
    *sorted(DOCS.rglob("*.md")),
]

# Relative link targets we allow to be missing (external-ish or generated media).
SKIP_LINK_SUFFIXES = (".gif", ".mp4", ".png", ".jpg", ".svg", ".json")

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_env_reference", GEN_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Ensure repeated loads in the same pytest process do not fight sys.modules
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_env_reference_matches_settings() -> None:
    """docs/ENV_REFERENCE.md must be regenerated after Settings field changes."""
    gen = _load_generator()
    expected = gen.render_markdown()
    assert ENV_REF.is_file(), "docs/ENV_REFERENCE.md missing — run generate_env_reference.py"
    actual = ENV_REF.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ENV_REFERENCE.md is stale; run: " "uv run python scripts/generate_env_reference.py"
    )


def test_env_reference_lists_every_settings_field() -> None:
    text = ENV_REF.read_text(encoding="utf-8")
    for name in Settings.model_fields:
        env = f"AUTO_BI_{name.upper()}"
        assert f"`{env}`" in text, f"ENV_REFERENCE missing {env}"


def test_env_example_covers_settings_keys() -> None:
    """Every Settings field should appear as AUTO_BI_* in .env.example (commented ok)."""
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing: list[str] = []
    for name in Settings.model_fields:
        env = f"AUTO_BI_{name.upper()}"
        if env not in example:
            missing.append(env)
    assert (
        not missing
    ), ".env.example missing Settings keys (add or comment them):\n  " + "\n  ".join(missing)


def test_settings_fields_have_descriptions() -> None:
    """Every Settings field needs a description so ENV_REFERENCE can avoid generic Notes."""
    missing: list[str] = []
    for name, field in Settings.model_fields.items():
        desc = field.description
        if desc is None or not str(desc).strip():
            missing.append(name)
    assert not missing, (
        "Settings fields missing description (ENV_REFERENCE otherwise uses generic Notes):\n  "
        + "\n  ".join(missing)
    )


def test_current_state_exists_and_is_linked() -> None:
    assert CURRENT_STATE.is_file()
    body = CURRENT_STATE.read_text(encoding="utf-8")
    assert "CURRENT_STATE" in body or "текущее состояние" in body.lower()
    # Public entry points point here
    readme = README.read_text(encoding="utf-8")
    assert "CURRENT_STATE" in readme or "docs/CURRENT_STATE.md" in readme
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    assert "CURRENT_STATE" in plan


def test_architecture_current_history_split_is_structural() -> None:
    """ARCHITECTURE holds current design; diary/session markers belong in HISTORY."""
    problems: list[str] = []

    arch_text = ARCHITECTURE.read_text(encoding="utf-8")
    state_text = CURRENT_STATE.read_text(encoding="utf-8")
    arch_lines = arch_text.splitlines()
    head = arch_lines[:20]
    head_blob = "\n".join(head)

    for token in (
        "CURRENT_STATE.md",
        "PLAN.md",
        "adr/",
        "ARCHITECTURE_HISTORY.md",
    ):
        if token not in head_blob:
            problems.append(f"ARCHITECTURE first-20 nav missing token: {token}")

    if not ARCHITECTURE_HISTORY.is_file():
        problems.append(f"missing history file: {ARCHITECTURE_HISTORY.relative_to(REPO)}")
    else:
        history_text = ARCHITECTURE_HISTORY.read_text(encoding="utf-8")
        history_head = "\n".join(history_text.splitlines()[:20])
        if not re.search(r"(?i)(?:history|истори)", history_head):
            problems.append("ARCHITECTURE_HISTORY first-20 lines must declare a history role")
        if "ARCHITECTURE.md" not in history_head:
            problems.append("ARCHITECTURE_HISTORY first-20 lines must link ARCHITECTURE.md")
        history_lines = history_text.splitlines()
        if len(history_lines) < 700:
            problems.append(
                "ARCHITECTURE_HISTORY must preserve the substantive pre-split archive "
                f"(found {len(history_lines)} lines)"
            )
        for anchor in ("## 1. Концепция", "### 3.20", "## 6. Риски"):
            if anchor not in history_text:
                problems.append(f"ARCHITECTURE_HISTORY missing frozen archive anchor: {anchor}")
        if "frozen pre-split snapshot" not in history_head.lower():
            problems.append("ARCHITECTURE_HISTORY first-20 lines must declare snapshot precedence")

    nav_lower = head_blob.lower()
    if "current vs history" in nav_lower and "residual" in nav_lower:
        problems.append(
            "ARCHITECTURE first-20 nav must not mix 'current vs history' with 'residual'"
        )

    if not re.search(
        r"(?i)ARCHITECTURE\.md.*(?:current|текущ)",
        state_text,
    ):
        problems.append(
            "CURRENT_STATE must mention ARCHITECTURE.md with current|текущ on the same line"
        )
    if not re.search(
        r"(?i)ARCHITECTURE_HISTORY\.md.*(?:history|истори)",
        state_text,
    ):
        problems.append(
            "CURRENT_STATE must mention ARCHITECTURE_HISTORY.md with "
            "history|истори on the same line"
        )

    def _strip_fenced_markdown_lines(lines: list[str]) -> list[tuple[int, str]]:
        """Drop fenced code blocks; keep (1-based line no, text) for diary scanning."""
        kept: list[tuple[int, str]] = []
        in_fence = False
        for i, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            kept.append((i, line))
        return kept

    prose = _strip_fenced_markdown_lines(arch_lines)

    marker_families: list[tuple[str, re.Pattern[str]]] = [
        ("ISO dates (YYYY-MM-DD)", re.compile(r"20\d\d-\d\d-\d\d")),
        ("Phase N", re.compile(r"Phase\s+\d", re.IGNORECASE)),
        (
            "ticket ids (S#, X-#, P#-#, L-#)",
            re.compile(r"\b(?:S\d+|X-\d+|P\d+-\d+|[A-Z]-\d+)\b"),
        ),
        ("_Реализовано", re.compile(r"_Реализовано", re.IGNORECASE)),
        ("задача N.N", re.compile(r"задача\s+\d+\.\d+", re.IGNORECASE)),
        ("Live-verified", re.compile(r"Live-verified", re.IGNORECASE)),
        ("live-провер variants", re.compile(r"live-провер\w*", re.IGNORECASE)),
        ("закрывает", re.compile(r"закрывает", re.IGNORECASE)),
        ("audit_", re.compile(r"audit_", re.IGNORECASE)),
        ("plan_sol", re.compile(r"plan_sol", re.IGNORECASE)),
    ]

    for label, pattern in marker_families:
        hits: list[str] = []
        for lineno, line in prose:
            for _match in pattern.finditer(line):
                snippet = line.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                hits.append(f"L{lineno}: {snippet}")
        if hits:
            examples = hits[:3]
            problems.append(
                f"diary marker family {label!r}: total={len(hits)}; "
                f"examples: " + " | ".join(examples)
            )

    invariant_section = re.search(
        r"Обязательные инварианты:\s*(.*?)(?=\n## 3\.)",
        arch_text,
        re.DOTALL,
    )
    if invariant_section is None:
        problems.append("ARCHITECTURE missing the mandatory invariants section")
    else:
        invariant_items = re.findall(r"(?m)^\d+\.\s+", invariant_section.group(1))
        if len(invariant_items) != 8:
            problems.append(
                "ARCHITECTURE mandatory invariants list must contain exactly 8 items "
                f"(found {len(invariant_items)})"
            )

    positive_contracts = {
        "lossless label join": (r"\blossless\b", r"\b0\.99\b", r"cardinality"),
        "ownership is not title-based": (r"\bownership\b", r"\btitle\b"),
        "durable build attempt": (r"\bbuild attempt\b", r"\bstable token\b"),
        "eval fingerprint": (r"\bfingerprint", r"\beval\b"),
        "native format pin": (r"reverse-engineered formats", r"contract tests"),
        "exact-SQL evidence isolation": (
            r"Evidence между разными statement не переиспользуется",
            r"cache miss обязателен",
        ),
    }
    for label, required_patterns in positive_contracts.items():
        missing_patterns = [
            pattern
            for pattern in required_patterns
            if re.search(pattern, arch_text, re.IGNORECASE) is None
        ]
        if missing_patterns:
            problems.append(
                f"ARCHITECTURE missing positive contract {label!r}: " + ", ".join(missing_patterns)
            )

    assert not problems, "architecture/history structural split problems:\n  " + "\n  ".join(
        problems
    )


def test_user_guide_points_to_env_reference() -> None:
    guide = USER_GUIDE.read_text(encoding="utf-8")
    assert "ENV_REFERENCE" in guide


def test_golden_fixture_count_matches_cases() -> None:
    ch = len(GOLDEN_CASES)
    gp = len(GP_GOLDEN_CASES)
    fixtures = list(FIXTURES_DIR.glob("*.json"))
    assert ch + gp == len(
        fixtures
    ), f"golden cases CH+GP={ch}+{gp}={ch + gp} but fixtures={len(fixtures)}"
    # CURRENT_STATE documents the counts — keep them honest
    state = CURRENT_STATE.read_text(encoding="utf-8")
    assert f"**{ch + gp}**" in state or str(ch + gp) in state
    assert str(ch) in state
    assert str(gp) in state
    assert str(len(ADVISOR_CASES)) in state
    assert str(len(GP_ADVISOR_CASES)) in state


def test_eval_fixtures_doc_mentions_v2_and_thresholds() -> None:
    text = (DOCS / "EVAL_FIXTURES.md").read_text(encoding="utf-8")
    assert "100%" in text or "100" in text
    assert "80%" in text or "80" in text
    assert "refresh-fingerprints" in text


def _resolve_md_target(source: Path, href: str) -> Path | None:
    """Return resolved path for a relative markdown link, or None if skipped."""
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith(("http://", "https://", "mailto:", "data:")):
        return None
    # strip anchor
    path_part = href.split("#", 1)[0]
    if not path_part:
        return None
    if path_part.endswith(SKIP_LINK_SUFFIXES):
        return None
    # Windows paths in docs are rare; treat as POSIX-relative
    target = (source.parent / path_part).resolve()
    try:
        target.relative_to(REPO.resolve())
    except ValueError:
        # Outside repo — ignore
        return None
    return target


def test_internal_markdown_links_resolve() -> None:
    """Broken relative links in public markdown fail CI."""
    broken: list[str] = []
    for md in PUBLIC_MD:
        if not md.is_file():
            continue
        # Skip generated env ref self-links? still check them.
        text = md.read_text(encoding="utf-8")
        for m in _MD_LINK.finditer(text):
            href = m.group(2).strip()
            # badges and raw.githubusercontent etc. are absolute https — skipped
            target = _resolve_md_target(md, href)
            if target is None:
                continue
            if target.is_file() or target.is_dir():
                continue
            # Allow links to paths that are only on GitHub (e.g. missing optional)
            broken.append(f"{md.relative_to(REPO)}: {href} -> missing {target}")
    assert not broken, "broken internal markdown links:\n  " + "\n  ".join(broken[:40])


def test_generator_check_mode_exits_zero() -> None:
    gen = _load_generator()
    code = gen.main(["--check"])
    assert code == 0


@pytest.mark.parametrize(
    "target",
    [
        "auto_bi/auth.py",
        "auto_bi/adapters/artifacts.py",
        "auto_bi/adapters/base.py",
        "auto_bi/errors.py",
        "auto_bi/config.py",
        "auto_bi/api/sessions.py",
        "auto_bi/store/db.py",
        "auto_bi/ir/validate.py",
        "auto_bi/ir/spec.py",
        "auto_bi/semantic/model.py",
        "auto_bi/semantic/select.py",
        "auto_bi/semantic/render.py",
        "auto_bi/semantic/prompt_data.py",
        "auto_bi/semantic/dbt_import.py",
        "auto_bi/agent/sql_guard.py",
        "auto_bi/agent/query_plan.py",
        "auto_bi/agent/dataset_plan.py",
        "auto_bi/agent/pipeline.py",
        "auto_bi/deployment_profile.py",
        "auto_bi/llm/budget.py",
        "auto_bi/api/ratelimit.py",
        "auto_bi/api/schemas.py",
        "auto_bi/advisor/findings.py",
        "auto_bi/advisor/clickhouse.py",
        "auto_bi/advisor/core.py",
        "auto_bi/advisor/explain.py",
        "auto_bi/advisor/greenplum.py",
        "auto_bi/introspect/clickhouse.py",
        "auto_bi/introspect/greenplum.py",
        "auto_bi/eval/cases.py",
        "auto_bi/eval/runner.py",
        "auto_bi/agent/sqlgen.py",
        "auto_bi/agent/insights.py",
        "auto_bi/adapters/datalens/chart_config.py",
        "auto_bi/adapters/datalens/dataset.py",
        "auto_bi/adapters/datalens/adapter.py",
    ],
)
def test_strict_targets_are_in_every_mypy_job_and_slo(target: str) -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    strict_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Mypy strict (boundary modules)"
    ]
    assert len(strict_steps) == 2
    assert all(target in step["run"] for step in strict_steps)
    assert f"`{target}`" in SLO.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "attr,expected",
    [
        ("send_samples", False),
        ("auth_enabled", False),
        ("demo_auto_only", False),
        ("profile", "local"),
        ("prune_on_rebuild", True),
    ],
)
def test_env_reference_documents_security_defaults(attr: str, expected: object) -> None:
    """Security-sensitive defaults appear as literal true/false/local in the generated table."""
    s = Settings(_env_file=None)
    assert getattr(s, attr) == expected
    env = f"AUTO_BI_{attr.upper()}"
    text = ENV_REF.read_text(encoding="utf-8")
    # find the table row for this env
    row_re = re.compile(rf"\| `{re.escape(env)}` \|[^|]+\| ([^|]+) \|")
    m = row_re.search(text)
    assert m, f"row for {env} not found"
    cell = m.group(1).strip()
    if isinstance(expected, bool):
        want = "true" if expected else "false"
        assert want in cell, f"{env} default cell {cell!r} should contain {want}"
    else:
        assert str(expected) in cell
