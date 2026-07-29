#!/usr/bin/env python
"""Generate docs/ENV_REFERENCE.md from Settings (plan_sol step 11).

Source of truth: ``auto_bi.config.Settings`` field names + defaults.
Purpose text is taken from ``Field(description=...)`` when present; otherwise a
short type note. Operators still use the curated tables in USER_GUIDE / .env.example;
this file is the complete machine-checked inventory.

Usage::

    uv run python scripts/generate_env_reference.py           # write docs/ENV_REFERENCE.md
    uv run python scripts/generate_env_reference.py --check   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

# scripts/ is not a package — import from installed auto_bi (uv run).
from auto_bi.config import Settings

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "docs" / "ENV_REFERENCE.md"
ENV_PREFIX = "AUTO_BI_"

# Non-prefixed companion vars operators also need (documented, not Settings fields).
EXTRA_ENV_ROWS: list[tuple[str, str, str]] = [
    (
        "ANTHROPIC_API_KEY",
        "Anthropic SDK key when ``llm_provider=anthropic``. "
        "Also accepted as ``AUTO_BI_ANTHROPIC_API_KEY``.",
        "(empty)",
    ),
]

SECRET_NAME_MARKERS = ("password", "api_key", "token", "secret")


def _format_default(name: str, value: Any) -> str:
    if any(m in name for m in SECRET_NAME_MARKERS) and value not in ("", None, 0, False):
        # Never print real secret-shaped defaults; Settings uses empty for secrets.
        return "(set; not printed)"
    if value is None:
        return "(unset / null)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if value == "":
            return "(empty)"
        if len(value) > 80:
            return f"`{value[:77]}…`"
        return f"`{value}`"
    if isinstance(value, int | float):
        return f"`{value}`"
    return f"`{value!r}`"


def _type_label(annotation: Any) -> str:
    if annotation is None:
        return "any"
    origin = get_origin(annotation)
    if origin is None:
        if hasattr(annotation, "__name__"):
            return annotation.__name__
        return str(annotation).replace("typing.", "")
    args = get_args(annotation)
    if origin is list:
        inner = _type_label(args[0]) if args else "any"
        return f"list[{inner}]"
    # Optional / Union with None
    non_none = [a for a in args if a is not type(None)]
    if type(None) in args and len(non_none) == 1:
        return f"{_type_label(non_none[0])} | null"
    return str(annotation).replace("typing.", "")


def _field_default(field: FieldInfo) -> Any:
    if field.default is not PydanticUndefined:
        return field.default
    if field.default_factory is not None:
        return field.default_factory()
    return PydanticUndefined


def _description(name: str, field: FieldInfo) -> str:
    if field.description:
        return field.description.strip()
    # Fallback: short structural note so the table is still useful without
    # re-annotating every Settings field with Field(description=...).
    return f"Settings field ``{name}`` (see ``auto_bi/config.py`` and ``.env.example``)."


def iter_settings_rows() -> list[tuple[str, str, str, str]]:
    """Return (env_name, type, default, purpose) sorted by env name."""
    rows: list[tuple[str, str, str, str]] = []
    for name, field in Settings.model_fields.items():
        env_name = f"{ENV_PREFIX}{name.upper()}"
        default = _field_default(field)
        default_s = "(required)" if default is PydanticUndefined else _format_default(name, default)
        rows.append(
            (
                env_name,
                _type_label(field.annotation),
                default_s,
                _description(name, field),
            )
        )
    rows.sort(key=lambda r: r[0])
    return rows


def render_markdown(rows: list[tuple[str, str, str, str]] | None = None) -> str:
    if rows is None:
        rows = iter_settings_rows()
    lines = [
        "# Environment reference (generated)",
        "",
        "> **Generated file.** Do not edit by hand.",
        "> Source of truth: `auto_bi.config.Settings`.",
        "> Regenerate: `uv run python scripts/generate_env_reference.py`",
        "> Check (CI): `uv run python scripts/generate_env_reference.py --check`",
        "",
        "Curated operator tables (short lists + prose) live in",
        "[USER_GUIDE §6](USER_GUIDE.md#6-конфигурация-переменные-окружения) and",
        "[`.env.example`](../.env.example). This page is the **complete** inventory of",
        f'`{ENV_PREFIX}*` keys consumed by Settings (`extra="ignore"` drops unknown',
        "names; typos are warned at `serve` startup).",
        "",
        "Deployment profile validation (`local` / `demo` / `production`) is described in",
        "[DEPLOYMENT §2](DEPLOYMENT.md).",
        "",
        "## Settings (`AUTO_BI_*`)",
        "",
        "| Variable | Type | Default | Notes |",
        "|---|---|---|---|",
    ]
    for env_name, typ, default, purpose in rows:
        # Escape pipes in purpose for markdown tables
        purpose_esc = purpose.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{env_name}` | `{typ}` | {default} | {purpose_esc} |")

    lines.extend(
        [
            "",
            "## Companion variables (not Settings fields)",
            "",
            "| Variable | Notes | Default |",
            "|---|---|---|",
        ]
    )
    for env_name, notes, default in EXTRA_ENV_ROWS:
        notes_esc = notes.replace("|", "\\|")
        lines.append(f"| `{env_name}` | {notes_esc} | {default} |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- Settings fields / `{ENV_PREFIX}*` keys: **{len(rows)}**",
            f"- Companion vars listed above: **{len(EXTRA_ENV_ROWS)}**",
            "",
            "<!-- generate_env_reference:end -->",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the file on disk differs from generated content",
    )
    args = parser.parse_args(argv)

    content = render_markdown()
    out: Path = args.out
    if args.check:
        if not out.is_file():
            print(f"missing {out} — run without --check to generate", file=sys.stderr)
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != content:
            print(
                f"stale {out}: regenerate with "
                "`uv run python scripts/generate_env_reference.py`",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {out} matches Settings ({len(iter_settings_rows())} keys)")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(iter_settings_rows())} Settings keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
