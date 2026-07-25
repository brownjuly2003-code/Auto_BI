"""Docs ratchet: README local-first ladder (offline → HF Space → full local).

Pathlib-only: no app imports, no network, no Settings.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"


def _howto_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Как пользоваться")
    end = text.index("## Документация", start)
    return text[start:end]


def test_readme_local_first_ladder() -> None:
    section = _howto_section()

    assert "uv run python scripts/demo_golden_path.py" in section
    assert "без DWH, BI, LLM и API-ключа" in section

    assert "HF Space" in section
    assert "auto-only" in section
    assert "пользовательский ключ не нужен" in section
    assert "текстовый режим там намеренно недоступен" in section

    assert ".env.example" in section
    assert "cp .env.example .env" in section
    assert "Copy-Item .env.example .env" in section
    assert "ANTHROPIC_API_KEY" in section
    assert "AUTO_BI_LLM_PROVIDER=gracekelly" in section
    assert "AUTO_BI_GRACEKELLY_URL" in section
    assert "AUTO_BI_CH_HOST" in section
    assert "AUTO_BI_CH_PASSWORD" in section
    assert "AUTO_BI_SUPERSET_URL" in section
    assert "AUTO_BI_SUPERSET_PASSWORD" in section
    assert "docs/ENV_REFERENCE.md" in section
    assert "docker compose up -d" in section
    assert "только ClickHouse и Superset" in section
    assert "не Auto_BI" in section
    assert "auto_bi serve" in section
    assert "127.0.0.1:8200" in section

    assert "pip install autobi-agent" in section
    assert "auto_bi introspect" in section
    assert 'auto_bi build "Выручка по магазинам за июнь 2026"' in section
    assert "auto_bi build --auto dm.sales_daily" in section
