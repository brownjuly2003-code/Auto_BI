"""dbt-импорт (task 2.6): manifest/catalog -> descriptions и relationships.

dbt is an ENRICHMENT source, not a schema source (ARCHITECTURE §3.1): the
introspector owns what tables/columns/types exist, model.yaml is hand-edited
after generation. Hence the merge policy: only EMPTY descriptions/fk are
filled, a hand-written value always wins, and dbt models/columns that do not
match the semantic model are reported, never added. The whole import is
deterministic — same artifacts + same model => same result and report.

Matching: a dbt node maps to `<schema>.<alias or name>`; the semantic model
keys tables the same way ("dm.sales_daily"). Relationships tests
(`test_metadata.name == "relationships"`) become joins (left = tested model's
column, right = `to`-model's `field`) and fill `Column.fk` on the left column.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from auto_bi.semantic.model import Join, SemanticModel

RELATIONSHIP_JOIN_TYPE = "many_to_one"  # dbt relationships test asserts exactly this shape


@dataclass
class DbtImportReport:
    table_descriptions: list[str] = field(default_factory=list)  # "dm.t"
    column_descriptions: list[str] = field(default_factory=list)  # "dm.t.col"
    joins_added: list[str] = field(default_factory=list)  # "dm.a.x -> dm.b.y"
    fks_set: list[str] = field(default_factory=list)  # "dm.a.x"
    kept_existing: list[str] = field(default_factory=list)  # non-empty values dbt did NOT touch
    unmatched_models: list[str] = field(default_factory=list)  # dbt models absent from the model
    unmatched_columns: list[str] = field(default_factory=list)  # dbt columns absent from tables

    @property
    def changed(self) -> int:
        return (
            len(self.table_descriptions)
            + len(self.column_descriptions)
            + len(self.joins_added)
            + len(self.fks_set)
        )


def load_artifact(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _node_table(node: dict) -> str:
    """dbt node -> fully qualified semantic-model table name ("dm.sales_daily").

    Models materialize under `alias` (defaults to name); sources point at the
    physical table via `identifier` — ignoring it mismatches every source whose
    identifier differs from its name (F7, phase-2 audit).
    """
    relation = node.get("alias") or node.get("identifier") or node.get("name", "")
    return f"{node.get('schema', '')}.{relation}"


def _model_nodes(manifest: dict) -> dict[str, dict]:
    """node_id -> node for models AND sources (both can describe DM tables)."""
    nodes = {
        node_id: node
        for node_id, node in manifest.get("nodes", {}).items()
        if node.get("resource_type") == "model"
    }
    nodes.update(manifest.get("sources", {}))
    return nodes


def _catalog_comment(catalog: dict | None, node_id: str, column: str) -> str:
    if catalog is None:
        return ""
    for section in ("nodes", "sources"):
        node = catalog.get(section, {}).get(node_id)
        if node is None:
            continue
        for name, info in node.get("columns", {}).items():
            if name.lower() == column.lower():
                return (info.get("comment") or "").strip()
    return ""


def _relationship_tests(manifest: dict, models: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """-> (left_table, left_column, right_table, right_column), deterministic order.

    `attached_node` (dbt >= 1.4) names the model under test; the other model in
    depends_on is the relationship target named by `to: ref(...)`. Tests without
    attached_node or with an unresolvable target are skipped silently — a missing
    join is recoverable by hand, a wrong one poisons SQL_GEN.
    """
    out: list[tuple[str, str, str, str]] = []
    for node_id in sorted(manifest.get("nodes", {})):
        node = manifest["nodes"][node_id]
        if node.get("resource_type") != "test":
            continue
        meta = node.get("test_metadata") or {}
        if meta.get("name") != "relationships":
            continue
        kwargs = meta.get("kwargs") or {}
        left_column = kwargs.get("column_name", "")
        right_column = kwargs.get("field", "")
        attached = node.get("attached_node", "")
        left_node = models.get(attached)
        targets = [
            dep
            for dep in (node.get("depends_on") or {}).get("nodes", [])
            if dep != attached and dep in models
        ]
        if not (left_node and left_column and right_column and len(targets) == 1):
            continue
        out.append(
            (_node_table(left_node), left_column, _node_table(models[targets[0]]), right_column)
        )
    return out


def dbt_enrich(
    model: SemanticModel, manifest: dict, catalog: dict | None = None
) -> DbtImportReport:
    """Merge dbt artifacts into the model IN PLACE; returns what changed and what didn't."""
    report = DbtImportReport()
    models = _model_nodes(manifest)

    for node_id in sorted(models):
        node = models[node_id]
        table = model.table(_node_table(node))
        if table is None:
            report.unmatched_models.append(_node_table(node))
            continue

        node_description = (node.get("description") or "").strip()
        if node_description:
            if table.description:
                report.kept_existing.append(table.name)
            else:
                table.description = node_description
                report.table_descriptions.append(table.name)

        for column_name, column_info in (node.get("columns") or {}).items():
            column = table.column(column_name)
            if column is None:
                report.unmatched_columns.append(f"{table.name}.{column_name}")
                continue
            description = (column_info.get("description") or "").strip() or _catalog_comment(
                catalog, node_id, column_name
            )
            if not description:
                continue
            if column.description:
                report.kept_existing.append(f"{table.name}.{column.name}")
            else:
                column.description = description
                report.column_descriptions.append(f"{table.name}.{column.name}")

    existing_joins = {(j.left, j.right) for j in model.joins}
    for left_table, left_column, right_table, right_column in _relationship_tests(manifest, models):
        table = model.table(left_table)
        column = table.column(left_column) if table else None
        right = model.table(right_table)
        if table is None or column is None or right is None or right.column(right_column) is None:
            report.unmatched_columns.append(f"{left_table}.{left_column} -> {right_table}")
            continue
        left_ref = f"{left_table}.{left_column}"
        right_ref = f"{right_table}.{right_column}"
        if (left_ref, right_ref) not in existing_joins:
            model.joins.append(Join(left=left_ref, right=right_ref, type=RELATIONSHIP_JOIN_TYPE))
            existing_joins.add((left_ref, right_ref))
            report.joins_added.append(f"{left_ref} -> {right_ref}")
        if column.fk:
            report.kept_existing.append(f"{left_ref} (fk)")
        else:
            column.fk = right_ref
            report.fks_set.append(left_ref)

    return report
