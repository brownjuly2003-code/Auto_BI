"""dbt-импорт (task 2.6): merge-политика fill-empty-only, relationships -> joins/fk,
unmatched-отчётность, идемпотентность, CLI dry-run/запись."""

import json

from auto_bi.cli import main
from auto_bi.semantic.dbt_import import dbt_enrich
from auto_bi.semantic.model import Join, SemanticModel

MANIFEST = {
    "nodes": {
        "model.shop.sales_daily": {
            "resource_type": "model",
            "schema": "dm",
            "name": "sales_daily",
            "description": "Дневные продажи из dbt",
            "columns": {
                "date": {"description": "День продажи (dbt)"},
                "orders": {"description": "Число заказов (dbt)"},
                "revenue": {"description": "Выручка из dbt — НЕ должна перетереть ручную"},
                "discount": {"description": "Скидка"},  # нет в model.yaml
            },
        },
        "model.shop.stores": {
            "resource_type": "model",
            "schema": "dm",
            "name": "stores",
            "description": "",
            "columns": {"city": {"description": ""}},  # пусто в manifest -> catalog comment
        },
        "model.shop.abandoned": {
            "resource_type": "model",
            "schema": "dm",
            "name": "abandoned",
            "description": "Модели нет в semantic model",
            "columns": {},
        },
        "test.shop.relationships_sales_store": {
            "resource_type": "test",
            "test_metadata": {
                "name": "relationships",
                "kwargs": {"column_name": "store_id", "field": "id"},
            },
            "attached_node": "model.shop.sales_daily",
            "depends_on": {"nodes": ["model.shop.sales_daily", "model.shop.stores"]},
        },
        "test.shop.not_null_sales_revenue": {
            "resource_type": "test",
            "test_metadata": {"name": "not_null", "kwargs": {"column_name": "revenue"}},
            "attached_node": "model.shop.sales_daily",
            "depends_on": {"nodes": ["model.shop.sales_daily"]},
        },
    },
    "sources": {},
}

CATALOG = {
    "nodes": {
        "model.shop.stores": {
            "columns": {"CITY": {"type": "String", "comment": "Город из catalog"}}
        }
    }
}


def test_fill_empty_only(demo_model) -> None:
    # demo_model: dm.sales_daily.description заполнено, у date/orders описаний нет,
    # у revenue описание есть — оно должно остаться ручным
    report = dbt_enrich(demo_model, MANIFEST, CATALOG)
    sales = demo_model.table("dm.sales_daily")
    assert sales.description == "Дневные продажи"  # ручное, dbt не перетёр
    assert "dm.sales_daily" in report.kept_existing
    assert sales.column("date").description == "День продажи (dbt)"
    assert sales.column("orders").description == "Число заказов (dbt)"
    assert sales.column("revenue").description == "Выручка, руб"  # ручное выиграло
    assert "dm.sales_daily.revenue" in report.kept_existing
    assert set(report.column_descriptions) >= {"dm.sales_daily.date", "dm.sales_daily.orders"}


def test_catalog_comment_fallback_case_insensitive(demo_model) -> None:
    dbt_enrich(demo_model, MANIFEST, CATALOG)
    assert demo_model.table("dm.stores").column("city").description == "Город из catalog"


def test_relationships_to_join_and_fk(demo_model) -> None:
    # demo_model уже содержит join sales->stores и fk на store_id: дедуп, не дубль
    report = dbt_enrich(demo_model, MANIFEST, None)
    assert report.joins_added == []  # join уже был
    assert "dm.sales_daily.store_id (fk)" in report.kept_existing

    bare = demo_model.model_copy(deep=True)
    bare.joins = []
    bare.table("dm.sales_daily").column("store_id").fk = None
    report = dbt_enrich(bare, MANIFEST, None)
    assert report.joins_added == ["dm.sales_daily.store_id -> dm.stores.id"]
    assert bare.joins == [Join(left="dm.sales_daily.store_id", right="dm.stores.id")]
    assert bare.table("dm.sales_daily").column("store_id").fk == "dm.stores.id"
    assert report.fks_set == ["dm.sales_daily.store_id"]


def test_unmatched_reported_not_added(demo_model) -> None:
    n_tables = len(demo_model.tables)
    report = dbt_enrich(demo_model, MANIFEST, None)
    assert "dm.abandoned" in report.unmatched_models
    assert "dm.sales_daily.discount" in report.unmatched_columns
    assert len(demo_model.tables) == n_tables  # dbt не источник схемы
    assert demo_model.table("dm.sales_daily").column("discount") is None


def test_idempotent_second_run(demo_model) -> None:
    dbt_enrich(demo_model, MANIFEST, CATALOG)
    second = dbt_enrich(demo_model, MANIFEST, CATALOG)
    assert second.changed == 0


def test_cli_dry_run_then_write(demo_model, tmp_path, capsys) -> None:
    model_path = tmp_path / "model.yaml"
    demo_model.dump(model_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(CATALOG), encoding="utf-8")

    args = [
        "dbt-import",
        "--manifest",
        str(manifest_path),
        "--catalog",
        str(catalog_path),
        "--model-path",
        str(model_path),
    ]
    assert main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert SemanticModel.load(model_path).table("dm.sales_daily").column("date").description == ""

    assert main(args) == 0
    reloaded = SemanticModel.load(model_path)
    assert reloaded.table("dm.sales_daily").column("date").description == "День продажи (dbt)"
    assert reloaded.table("dm.stores").column("city").description == "Город из catalog"

    # повторный прогон — идемпотентен
    assert main(args) == 0
    assert "Изменений нет" in capsys.readouterr().out


def test_cli_missing_files(tmp_path) -> None:
    assert main(["dbt-import", "--manifest", str(tmp_path / "no.json")]) == 2
