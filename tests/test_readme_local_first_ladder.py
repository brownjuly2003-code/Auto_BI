"""Docs ratchet: README exposes only the two current local-first paths.

Supported paths: offline golden path + full LOCAL_BYOK. Excluded external demo
paths must not reappear as onboarding or current product status.

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

    # Offline golden path (supported).
    assert "uv run python scripts/demo_golden_path.py" in section
    assert "без DWH, BI, LLM и API-ключа" in section

    # Excluded external demo paths are not onboarding steps or current status.
    assert "HF Space" not in section
    assert "Hugging Face" not in section
    assert "hf.space" not in section

    # Negative: superseded claims from the removed external demo route.
    assert "пользовательский ключ не нужен" not in section
    assert "текстовый режим там намеренно недоступен" not in section

    # Full LOCAL_BYOK path (supported).
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

    # The offline demo validates the full IR but prints SQL for two representative charts.
    assert "валидированный SQL по каждому чарту" not in section
    assert "скомпилированные примеры SQL" in section
