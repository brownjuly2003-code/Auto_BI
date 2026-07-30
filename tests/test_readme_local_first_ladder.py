"""Docs ratchet: README local-first ladder under owner HF de-scope.

Supported paths: offline golden path + full LOCAL_BYOK. HF Space is
historical/not supported — not a verified current auto-only launch path.

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

    # HF entry: historical / not supported (owner de-scope), not a working route.
    assert "HF Space" in section
    assert "исторический" in section
    assert "не supported" in section
    assert "stale vs v0.5.0" in section
    assert "0.4.0" in section
    assert "demo_auto_only=false" in section
    assert "no capabilities" in section
    # Not a verified current auto-only launch / onboarding path.
    assert "auto-only" in section
    assert "onboarding" in section

    # Negative: superseded claims that HF is a supported auto-only route.
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
