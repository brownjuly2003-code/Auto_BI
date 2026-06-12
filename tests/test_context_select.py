"""Context selection (task 1.5): deterministic table picking under the prompt budget."""

from auto_bi.agent.propose import build_propose_prompt
from auto_bi.semantic.model import Column, ColumnRole, Join, SemanticModel, Table
from auto_bi.semantic.render import render_model
from auto_bi.semantic.select import PROMPT_CHAR_BUDGET, select_context


def _table(name: str, description: str = "", n_cols: int = 5, top_values: bool = False) -> Table:
    columns = [
        Column(
            name=f"col_{i}",
            type="String",
            role=ColumnRole.DIMENSION,
            description=f"Колонка номер {i} таблицы {name}",
            top_values=[f"значение_{i}_{j}" for j in range(10)] if top_values else [],
        )
        for i in range(n_cols)
    ]
    return Table(name=name, description=description, columns=columns)


SALES = Table(
    name="dm.sales_daily",
    description="Дневные продажи по магазинам",
    columns=[
        Column(name="date", type="Date", role=ColumnRole.TIME),
        Column(name="revenue", type="Decimal", role=ColumnRole.MEASURE, description="Выручка"),
    ],
)
STORES = Table(
    name="dm.stores",
    description="Справочник магазинов",
    columns=[
        Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
        Column(name="city", type="String", role=ColumnRole.DIMENSION, description="Город"),
    ],
)
HR = Table(
    name="hr.salaries",
    description="Зарплаты сотрудников",
    columns=[Column(name="salary", type="Decimal", role=ColumnRole.MEASURE)],
)


def test_small_model_is_identity() -> None:
    model = SemanticModel(tables=[SALES, STORES, HR])
    out = select_context(model, "выручка по магазинам", budget_chars=100_000)
    assert [t.name for t in out.tables] == ["dm.sales_daily", "dm.stores", "hr.salaries"]


def test_relevant_tables_win_under_budget() -> None:
    # budget fits roughly two tables: the request mentions выручка + магазины,
    # so sales and stores must survive and HR must be dropped
    filler = [_table(f"dm.filler_{i}", "Технические данные загрузок", n_cols=15) for i in range(5)]
    model = SemanticModel(tables=[HR, *filler, SALES, STORES])
    budget = len(render_model(SemanticModel(tables=[SALES, STORES]))) + 40
    out = select_context(model, "выручка по магазинам и городам", budget_chars=budget)
    names = [t.name for t in out.tables]
    assert "dm.sales_daily" in names
    assert "dm.stores" in names
    assert "hr.salaries" not in names


def test_inflections_match_via_stem() -> None:
    # «магазинам» (dative plural) must still hit «магазинов»/«магазины»
    model = SemanticModel(tables=[STORES, HR])
    out = select_context(model, "сколько магазинам завезли товара", budget_chars=200)
    assert out.tables[0].name == "dm.stores"


def test_value_mention_pulls_table() -> None:
    products = Table(
        name="dm.products",
        columns=[
            Column(
                name="category",
                type="String",
                role=ColumnRole.DIMENSION,
                top_values=["Алкоголь", "Заморозка"],
            )
        ],
    )
    model = SemanticModel(tables=[HR, products])
    out = select_context(model, "продажи алкоголя", budget_chars=300)
    assert out.tables[-1].name == "dm.products"
    assert all(t.name != "hr.salaries" for t in out.tables) or len(out.tables) == 2


def test_joins_survive_only_with_both_endpoints() -> None:
    join_kept = Join(left="dm.sales_daily.store_id", right="dm.stores.id")
    join_dropped = Join(left="dm.sales_daily.manager_id", right="hr.salaries.employee_id")
    model = SemanticModel(tables=[SALES, STORES, HR], joins=[join_kept, join_dropped])
    budget = len(render_model(SemanticModel(tables=[SALES, STORES]))) + 40
    out = select_context(model, "выручка магазинов по городам", budget_chars=budget)
    assert out.joins == [join_kept]


def test_samples_dropped_before_table_dropped() -> None:
    fat = _table("dm.fat", "продажи и выручка", n_cols=10, top_values=True)
    with_samples = len(render_model(SemanticModel(tables=[fat])))
    without = len(render_model(SemanticModel(tables=[fat]), include_samples=False))
    assert without < with_samples
    out = select_context(
        model=SemanticModel(tables=[fat]), request="выручка", budget_chars=without + 2
    )
    assert [t.name for t in out.tables] == ["dm.fat"]


def test_top_table_always_included_even_over_budget() -> None:
    out = select_context(SemanticModel(tables=[SALES]), "выручка", budget_chars=10)
    assert [t.name for t in out.tables] == ["dm.sales_daily"]


def test_propose_prompt_fits_global_budget_on_huge_model() -> None:
    huge = SemanticModel(
        tables=[
            _table(f"dm.wide_{i}", f"Витрина номер {i} про показатели", n_cols=40, top_values=True)
            for i in range(120)
        ]
    )
    prompt = build_propose_prompt("выручка по магазинам", huge)
    assert len(prompt) <= PROMPT_CHAR_BUDGET
