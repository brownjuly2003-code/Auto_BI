"""DataLens adapter unit tests — createConnection/createDataset payload SHAPES + client
transport on httpx.MockTransport.

These pin the deterministic IR->payload mapping (reversal doc §3-4); the numbers and the
exact signin route are confirmed later by a live contract test on the Mac stand
(integration, mirrors tests/test_superset_contract.py). No stand needed here.
"""

from __future__ import annotations

import httpx
import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.client import DataLensAPIError, DataLensClient
from auto_bi.adapters.datalens.dataset import (
    _user_type,
    build_connection_payload,
    build_dataset_payload,
    dataset_name,
    safe_entry_name,
)
from auto_bi.ir.spec import ChartQuery, JoinSpec, Measure, column_alias
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Physical,
    SemanticModel,
    Table,
)

CH_DWH = DWHConfig(
    host="host.docker.internal", port=8123, database="dm", user="auto_bi_ro", password="pw"
)
GP_DWH = DWHConfig(
    host="host.docker.internal",
    port=5433,
    database="dm",
    user="auto_bi_ro",
    password="pw",
    engine="greenplum",
)


# --- type mapping -----------------------------------------------------------


def test_user_type_clickhouse_spellings() -> None:
    assert _user_type("String") == "string"
    assert _user_type("LowCardinality(String)") == "string"
    assert _user_type("Nullable(String)") == "string"
    assert _user_type("UInt32") == "integer"
    assert _user_type("Int64") == "integer"
    assert _user_type("Nullable(Int64)") == "integer"
    assert _user_type("Float64") == "float"
    assert _user_type("Decimal(18, 2)") == "float"
    assert _user_type("Date") == "date"
    assert _user_type("DateTime") == "genericdatetime"  # DataLens enum, not "datetime"
    assert _user_type("DateTime64(3)") == "genericdatetime"
    assert _user_type("Bool") == "boolean"


def test_user_type_postgres_spellings() -> None:
    assert _user_type("integer") == "integer"
    assert _user_type("bigint") == "integer"
    assert _user_type("numeric") == "float"
    assert _user_type("double precision") == "float"
    assert _user_type("text") == "string"
    assert _user_type("character varying") == "string"
    assert _user_type("date") == "date"
    assert _user_type("timestamp without time zone") == "genericdatetime"
    assert _user_type("boolean") == "boolean"


# --- connection payload -----------------------------------------------------


def test_connection_payload_clickhouse() -> None:
    body = build_connection_payload(CH_DWH, name="auto_bi__ch", workbook_id="wb1")
    assert body["type"] == "clickhouse"
    assert body["host"] == "host.docker.internal"
    assert body["port"] == 8123
    assert body["username"] == "auto_bi_ro"
    assert body["secure"] == "off"
    assert body["raw_sql_level"] == "subselect"  # required for dataset-from-SQL
    assert body["cache_ttl_sec"] is None
    assert body["workbook_id"] == "wb1"
    # field is `type`, never `db_type` (reversal §3)
    assert "db_type" not in body


def test_connection_payload_greenplum_type() -> None:
    body = build_connection_payload(GP_DWH, name="auto_bi__gp", workbook_id="wb1")
    assert body["type"] == "greenplum"
    assert body["port"] == 5433


def test_connection_payload_unknown_engine_raises() -> None:
    bad = DWHConfig(host="h", port=1, database="d", user="u", password="p", engine="oracle")
    with pytest.raises(ValueError, match="connection type"):
        build_connection_payload(bad, name="x", workbook_id="wb1")


# --- dataset payload --------------------------------------------------------


def test_dataset_payload_shape_and_subselect(demo_model: SemanticModel) -> None:
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
    )
    body = build_dataset_payload(
        query, demo_model, workbook_id="wb1", connection_id="conn1", name="auto_bi__ds"
    )
    assert body["workbook_id"] == "wb1"  # snake_case — camelCase is silently ignored
    assert body["name"] == "auto_bi__ds"
    ds = body["dataset"]
    assert ds["avatar_relations"] == []
    assert ds["rls"] == {}
    assert ds["component_errors"] == {"items": []}

    source = ds["sources"][0]
    assert source["connection_id"] == "conn1"
    assert source["source_type"] == "CH_SUBSELECT"
    assert source["managed_by"] == "user"
    assert source["index_info_set"] == []
    assert "SELECT" in source["parameters"]["subsql"]
    # raw_schema columns are the SELECT aliases (date dim + measure alias), in order
    assert [c["name"] for c in source["raw_schema"]] == ["date", "Выручка"]
    assert all(c["native_type"] is None and c["nullable"] for c in source["raw_schema"])

    # one root avatar wired to the source
    avatar = ds["source_avatars"][0]
    assert avatar["source_id"] == source["id"]
    assert avatar["is_root"] is True


def test_dataset_result_schema_roles_and_types(demo_model: SemanticModel) -> None:
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="Выручка"),  # Decimal -> float
            Measure(column="orders", agg=Aggregation.SUM),  # UInt32 -> integer
            Measure(column="store_id", agg=Aggregation.COUNT_DISTINCT, label="n_stores"),
        ],
    )
    fields = {f["title"]: f for f in build_dataset_payload(
        query, demo_model, workbook_id="wb", connection_id="c", name="ds"
    )["dataset"]["result_schema"]}  # fmt: skip

    assert fields["date"]["type"] == "DIMENSION"
    assert fields["date"]["aggregation"] == "none"
    assert fields["date"]["data_type"] == "date"

    assert fields["Выручка"]["type"] == "MEASURE"
    assert fields["Выручка"]["aggregation"] == "sum"  # identity over pre-aggregated row
    assert fields["Выручка"]["data_type"] == "float"  # Decimal(18,2)
    assert fields["sum_orders"]["data_type"] == "integer"  # UInt32
    assert fields["n_stores"]["data_type"] == "integer"  # count_distinct
    # every field carries the same root avatar id and a stable guid
    avatar_ids = {f["avatar_id"] for f in fields.values()}
    assert len(avatar_ids) == 1
    assert all(f["calc_mode"] == "direct" and f["valid"] for f in fields.values())


def test_dataset_non_sum_measures_reaggregate_as_sum_identity(demo_model: SemanticModel) -> None:
    """AVG/MIN/MAX measures ALSO get DataLens aggregation "sum" — correct because the
    dataset subselect already computed the aggregate per group and the chart's group-by
    equals the dataset grain (one row per group), so SUM over a single value is identity.
    This pins the invariant the "sum" re-aggregation relies on (reversal §4 / F4)."""
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["store_id"],  # dataset grain == chart grain -> one row per group
        measures=[
            Measure(column="revenue", agg=Aggregation.AVG, label="avg_rev"),
            Measure(column="orders", agg=Aggregation.MAX, label="max_orders"),
        ],
    )
    fields = {f["title"]: f for f in build_dataset_payload(
        query, demo_model, workbook_id="wb", connection_id="c", name="ds"
    )["dataset"]["result_schema"]}  # fmt: skip
    assert fields["avg_rev"]["aggregation"] == "sum" and fields["avg_rev"]["data_type"] == "float"
    assert fields["max_orders"]["aggregation"] == "sum"
    # the dataset's dimension fields are exactly the chart's group columns (grain match,
    # which is what makes the sum-identity hold)
    dims = [f["title"] for f in fields.values() if f["type"] == "DIMENSION"]
    assert dims == [column_alias(c) for c in query.group_columns()]


def test_dataset_joined_dimension_resolves_type_from_joined_table(
    demo_model: SemanticModel,
) -> None:
    # city lives on dm.stores (LowCardinality(String)) reached via a join; the field is
    # addressed by its bare alias and typed from the joined table
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["dm.stores.city"],
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
        ],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
    )
    fields = {f["title"]: f for f in build_dataset_payload(
        query, demo_model, workbook_id="wb", connection_id="c", name="ds"
    )["dataset"]["result_schema"]}  # fmt: skip
    assert "city" in fields  # bare alias, not the qualified ref
    assert fields["city"]["type"] == "DIMENSION"
    assert fields["city"]["data_type"] == "string"  # LowCardinality(String) unwrapped


def test_dataset_payload_greenplum_uses_pg_source_and_dialect() -> None:
    gp_model = SemanticModel(
        tables=[
            Table(
                name="dm.sales",
                columns=[
                    Column(name="region", type="text", role=ColumnRole.DIMENSION),
                    Column(
                        name="amount", type="numeric", role=ColumnRole.MEASURE, agg=Aggregation.SUM
                    ),
                ],
                physical=Physical(engine="greenplum", distribution_key=["region"], rows=10_000_000),
            )
        ],
    )
    query = ChartQuery(
        table="dm.sales",
        dimensions=["region"],
        measures=[Measure(column="amount", agg=Aggregation.SUM, label="amt")],
    )
    body = build_dataset_payload(query, gp_model, workbook_id="wb", connection_id="c", name="ds_gp")
    source = body["dataset"]["sources"][0]
    assert source["source_type"] == "PG_SUBSELECT"
    # postgres dialect quotes identifiers with double quotes, not CH backticks
    assert '"region"' in source["parameters"]["subsql"]
    assert "`" not in source["parameters"]["subsql"]
    fields = {f["title"]: f for f in body["dataset"]["result_schema"]}
    assert fields["region"]["data_type"] == "string"
    assert fields["amt"]["data_type"] == "float"  # numeric


def test_dataset_ids_are_stable(demo_model: SemanticModel) -> None:
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
    )
    a = build_dataset_payload(query, demo_model, workbook_id="wb", connection_id="c", name="ds")
    b = build_dataset_payload(query, demo_model, workbook_id="wb", connection_id="c", name="ds")
    assert a == b  # deterministic: same name -> same source/avatar/field uuids


def test_dataset_name_unique_even_when_slugs_collide() -> None:
    a = dataset_name("Обзор", "chart-a")
    b = dataset_name("Обзор", "chart!a")  # slugs to the same "chart_a", different raw id
    assert a != b
    assert a.startswith("auto_bi__")


def test_safe_entry_name_strips_disallowed_and_edges() -> None:
    # brackets/slash/question-mark are outside the DataLens entry-name charset
    assert safe_entry_name("[dl-contract] dashboard") == "dl-contract dashboard"
    assert safe_entry_name("Продажи / Q2?") == "Продажи Q2"
    # Cyrillic + spaces + parens are all allowed -> unchanged
    assert safe_entry_name("Выручка по магазинам (idem)") == "Выручка по магазинам (idem)"
    # ':' is allowed inside but not at an edge -> kept inside, trimmed off the end
    assert safe_entry_name("Итого: продажи") == "Итого: продажи"
    assert safe_entry_name("-- Итого --") == "Итого"  # edge dashes/spaces trimmed
    # nothing valid -> fallback, made unique per title so two un-nameable titles differ
    assert safe_entry_name("###").startswith("Auto_BI_")
    assert safe_entry_name("###") != safe_entry_name("???")


# --- client transport -------------------------------------------------------


class FakeDataLens:
    """Just enough of the UI gateway: signin sets the `auth` cookie, gateway echoes."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        body = json.loads(request.content) if request.content else None
        cookie = request.headers.get("cookie", "")
        self.requests.append((path, cookie, body))
        if path.endswith("/auth/signin"):
            return httpx.Response(
                200, json={"done": True}, headers={"set-cookie": "auth=JWE.token; Path=/"}
            )
        if path.startswith("/gateway/root/"):
            return httpx.Response(200, json={"id": "entry123"})
        if path == "/ping":
            return httpx.Response(200, text="pong")
        return httpx.Response(404, json={"message": f"unexpected {path}"})


def _client(fake: FakeDataLens) -> DataLensClient:
    http = httpx.Client(base_url="http://dl.test", transport=httpx.MockTransport(fake))
    return DataLensClient("http://dl.test", "admin", "admin", http=http)


def test_client_gateway_logs_in_then_carries_auth_cookie() -> None:
    fake = FakeDataLens()
    result = _client(fake).gateway("bi", "createConnection", {"name": "x"})
    assert result == {"id": "entry123"}
    # first request is signin (no cookie yet), second is the gateway call WITH the cookie
    signin = next(r for r in fake.requests if r[0].endswith("/auth/signin"))
    gw = next(r for r in fake.requests if r[0] == "/gateway/root/bi/createConnection")
    assert signin[2] == {"login": "admin", "password": "admin"}
    assert "auth=JWE.token" in gw[1]


def test_client_login_without_cookie_raises() -> None:
    def no_cookie(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True})  # 200 but no Set-Cookie

    http = httpx.Client(base_url="http://dl.test", transport=httpx.MockTransport(no_cookie))
    client = DataLensClient("http://dl.test", "admin", "admin", http=http)
    with pytest.raises(DataLensAPIError, match="no `auth` cookie"):
        client.login()


def test_client_health() -> None:
    assert _client(FakeDataLens()).health() is True
