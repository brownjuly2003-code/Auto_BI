"""Render generated ClickHouse and PostgreSQL names and string values."""

from __future__ import annotations

from sqlglot import exp
from sqlglot.dialects.clickhouse import ClickHouse
from sqlglot.dialects.postgres import Postgres
from sqlglot.generators.clickhouse import ClickHouseGenerator
from sqlglot.generators.postgres import PostgresGenerator


class SQLRenderRefusal(ValueError):
    """Generated text contains NUL or a surrogate character."""


class _ClickHouseGenerator(ClickHouseGenerator):
    def identifier_sql(self, expression: exp.Identifier) -> str:
        escaped = expression.copy()
        escaped.set("this", expression.name.replace("\\", "\\\\"))
        return super().identifier_sql(escaped)


class _PostgresGenerator(PostgresGenerator):
    def literal_sql(self, expression: exp.Literal) -> str:
        if expression.is_string and "\\" in expression.this:
            value = expression.this.replace("\\", "\\\\").replace("'", "''")
            return f"E'{value}'"
        return super().literal_sql(expression)


def render_sql(expression: exp.Expression, *, dialect: str) -> str:
    """Render an AST with quoted identifiers; refuse NUL and surrogate characters."""
    for node in expression.walk():
        if isinstance(node, (exp.Identifier, exp.Literal, exp.ByteString)):
            value = node.this
            if isinstance(value, str) and any(
                char == "\0" or "\ud800" <= char <= "\udfff" for char in value
            ):
                raise SQLRenderRefusal(
                    f"generated SQL text {value!r} cannot contain NUL or surrogate characters"
                )
    if dialect == "clickhouse":
        return _ClickHouseGenerator(dialect=ClickHouse(), identify=True).generate(expression)
    if dialect == "postgres":
        return _PostgresGenerator(dialect=Postgres(), identify=True).generate(expression)
    raise ValueError(f"unsupported generated SQL dialect: {dialect!r}")


def quote_identifier(name: str, *, dialect: str) -> str:
    return render_sql(exp.to_identifier(name, quoted=True), dialect=dialect)
