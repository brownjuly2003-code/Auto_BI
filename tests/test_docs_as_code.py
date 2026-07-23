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


def test_current_state_exists_and_is_linked() -> None:
    assert CURRENT_STATE.is_file()
    body = CURRENT_STATE.read_text(encoding="utf-8")
    assert "CURRENT_STATE" in body or "текущее состояние" in body.lower()
    # Public entry points point here
    readme = README.read_text(encoding="utf-8")
    assert "CURRENT_STATE" in readme or "docs/CURRENT_STATE.md" in readme
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    assert "CURRENT_STATE" in plan


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


def test_ir_validate_is_in_every_strict_mypy_job_and_slo() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    strict_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Mypy strict (boundary modules)"
    ]
    assert len(strict_steps) == 2
    assert all("auto_bi/ir/validate.py" in step["run"] for step in strict_steps)
    assert "`auto_bi/ir/validate.py`" in SLO.read_text(encoding="utf-8")


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
