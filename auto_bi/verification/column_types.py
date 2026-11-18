"""The type a column's declared spelling names, as the engine prints it.

A model declares a column's type as free text, and an engine takes many spellings of one
type. `dwh.physical` compares the declared type with the one the engine's catalog holds by
the name the engine prints for it — `format_type` on a PostgreSQL kernel, `system.columns`
on ClickHouse — so another spelling of the same type is not a change of type (ADR 0010, F2;
ADR 0012, D1).

The rules are the engines', not the manuals': each spelling the tests read as another name of a
type is created live and read back the way the probe reads it
(`tests/test_decision_live_postgres.py` on PostgreSQL 16 and the 9.4.26 kernel,
`tests/test_decision_live_clickhouse.py` on ClickHouse 24.8), but for a ClickHouse family name
in another case (`int64`, `nullable(bigint)`): the engine refuses it, and the comparison reads it
in any case, as it read every name before D1. A spelling the rules do not read is
returned as written, blanks collapsed (and, on a PostgreSQL kernel, lowercased, as the grammar
folds a name it does not quote): the gate cannot place the type it names, and a comparison by it
errs towards `physical.type_drift` (review). A quoted name, and on ClickHouse a string, is
content character by character — `Enum8('a' = 1)` and `Enum8('A' = 1)` are two types — so no
blank or case is touched inside one; nor is a blank inside a name dropped (`Int 64` is not
`Int64`).

The type is free text, so every pattern here matches a text one way only — a pattern that
could match it two ways retries every split when the match fails.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

POSTGRES_DIALECT = "postgres"
CLICKHOUSE_DIALECT = "clickhouse"


def canonical_type(spelling: str, dialect: str, *, printed: bool = False) -> str:
    """The name the engine of `dialect` (a sqlglot dialect) prints for the type `spelling`
    names, but for a time precision of six digits or more on a PostgreSQL kernel, which is read
    as none (six digits is what a time type with no precision keeps); the spelling as written
    where the rules do not read it. `printed`: the spelling is the catalog's, which prints no
    serial type, so a serial name there names a type of its own (a domain may be called
    `serial4`)."""
    if dialect == POSTGRES_DIALECT:
        return _pg_type(spelling, printed)
    if dialect == CLICKHOUSE_DIALECT:
        return _ch_type(spelling)
    return " ".join(spelling.split())


def compared_name(spelling: str, dialect: str, *, printed: bool = False) -> str:
    """What a declared type and the catalog's compare equal by: `canonical_type`, without the
    blanks next to a parenthesis, a bracket, a comma or `=` outside a quote. No case is folded
    here: a name the rules read is in the engine's case already, and one they do not is
    compared as written."""
    quotes, escape = _QUOTES.get(dialect, _CH_QUOTES)
    return _outside(canonical_type(spelling, dialect, printed=printed), quotes, escape, _compact)


#: The quotes whose text is content, and whether a backslash escapes in them: a PostgreSQL
#: kernel quotes names (a doubled quote is one); ClickHouse quotes strings and names, with
#: backslash escapes in both.
_PG_QUOTES = ('"', False)
_CH_QUOTES = ("'\"`", True)
_QUOTES = {POSTGRES_DIALECT: _PG_QUOTES, CLICKHOUSE_DIALECT: _CH_QUOTES}
_BLANKS = re.compile(r"\s+")
_MARK_BLANKS = re.compile(r" ?([(),\[\]=]) ?")


def _blanks(text: str, quotes: str, escape: bool) -> str:
    """`text` with each run of blanks outside a quote one blank, and none at its ends."""
    return _outside(text, quotes, escape, lambda part: _BLANKS.sub(" ", part)).strip()


def _compact(part: str) -> str:
    return _MARK_BLANKS.sub(r"\1", part)


def _outside(text: str, quotes: str, escape: bool, change: Callable[[str], str]) -> str:
    """`text` with `change` applied to each run of it outside a quote."""
    parts: list[str] = []
    start = at = 0
    while at < len(text):
        if text[at] not in quotes:
            at += 1
            continue
        parts.append(change(text[start:at]))
        end = _quote_end(text, at, escape)
        parts.append(text[at:end])
        start = at = end
    parts.append(change(text[start:]))
    return "".join(parts)


def _quote_end(text: str, at: int, escape: bool) -> int:
    """Where the quote opening at `at` ends: past its closing quote — with `escape`, a character
    after a backslash is inside it — or at the end of `text`. A doubled quote reads as one quote
    closed and the next opened, and the text of both is kept as written all the same."""
    quote, end = text[at], at + 1
    while end < len(text):
        if escape and text[end] == "\\":
            end += 2
        elif text[end] != quote:
            end += 1
        else:
            return end + 1
    return len(text)


# --- PostgreSQL kernel -------------------------------------------------------------------

#: PostgreSQL's other names for a type -> the name `format_type` prints: the "Aliases" column
#: of the manual's table of data types but the serial types, `dec` and `char varying`, which
#: its grammar reads the same way, and the `national` and `nchar` forms of the character types.
_PG_ALIASES = {
    "int": "integer",
    "int4": "integer",
    "int2": "smallint",
    "int8": "bigint",
    "float4": "real",
    "float8": "double precision",
    "bool": "boolean",
    "dec": "numeric",
    "decimal": "numeric",
    "char": "character",
    "varchar": "character varying",
    "char varying": "character varying",
    "national character": "character",
    "national char": "character",
    "nchar": "character",
    "national character varying": "character varying",
    "national char varying": "character varying",
    "nchar varying": "character varying",
    "varbit": "bit varying",
}
#: The two names that carry their zone in them.
_PG_ZONED = {"timestamptz": "timestamp", "timetz": "time"}
#: A serial column is a column of this type with a sequence behind it; `format_type` prints the
#: type. Read only bare (or quoted): `pg_catalog.serial` names no type, an array of serial is
#: refused.
_PG_SERIAL = {
    "serial": "integer",
    "serial4": "integer",
    "bigserial": "bigint",
    "serial8": "bigint",
    "smallserial": "smallint",
    "serial2": "smallint",
}
#: Names only the grammar's keywords know. Quoted or schema-qualified, a name is looked up in the
#: catalog as written, and these name no type there (`"char"` is another type, read apart).
_PG_KEYWORD_ONLY = frozenset(
    {
        "int",
        "integer",
        "smallint",
        "bigint",
        "real",
        "float",
        "double",
        "decimal",
        "dec",
        "boolean",
        "character",
        "national",
        "nchar",
    }
)
#: Catalog names a quoted or schema-qualified spelling may give a modifier to, as the grammar
#: reads it for the keyword: any other one is not read (`pg_catalog.interval(3)` is refused).
_PG_GENERIC_MODIFIED = frozenset(
    {"numeric", "varchar", "bpchar", "bit", "varbit", "timestamp", "timestamptz", "time", "timetz"}
)
#: The most fractional digits of a second PostgreSQL keeps, and so what a time type without a
#: precision keeps; a precision above it is cut to it.
_PG_TIME_PRECISION = 6
#: The largest precision the grammar reads as a number; a longer one is a syntax error.
_PG_INT_MAX = 2**31 - 1

#: A PostgreSQL type as a model may write it, lowercased with single blanks: a name of one or
#: more words, a modifier, the zone of a time type, array brackets. Each repeated part matches
#: its text one way only, and the zone, which the name could also take, occurs once: a blank two
#: repeats could take makes a failed match retry every split, 2^n tries for n repeats (`[ ]` was
#: such a blank, before or after an empty bound).
_PG_SPELLING = re.compile(
    r"(?P<name>[a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)*?)"
    r"(?: ?\((?P<modifier>[^()]*)\))?"
    r"(?P<zone> with(?:out)? time zone)?"
    r"(?P<array>(?: ?\[ ?(?:[0-9]+ ?)?\])*)"
)
#: A quoted or `pg_catalog`-qualified name, read before the spelling is lowercased: a quoted
#: name keeps its case, and the catalog's names are lowercase. The name ends where an
#: identifier cannot go on and no dot follows it, so the schema, the name and the rest split
#: one way only.
_PG_GENERIC = re.compile(
    r'(?:(?P<schema>"pg_catalog"|(?i:pg_catalog)) ?\. ?)?'
    r'(?:"(?P<quoted>[^"]*)"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))'
    r'(?P<rest>(?![A-Za-z0-9_]| ?\.)[^"]*)'
)
#: What may follow a quoted or qualified name: a modifier and array brackets.
_PG_GENERIC_TAIL = re.compile(
    r"(?: ?\((?P<modifier>[^()]*)\))?(?P<array>(?: ?\[ ?(?:[0-9]+ ?)?\])*)"
)
_PG_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
#: The bound the SQL `ARRAY` keyword may take: one, of digits.
_PG_ARRAY_BOUND = re.compile(r"(?: ?\[ ?[0-9]+ ?\])?")


def _pg_type(spelling: str, printed: bool) -> str:
    """A PostgreSQL type as `format_type` prints it, from the spellings of it the rules read:
    `int4` is `integer`, `pg_catalog.int4` and `"int4"` too, `serial` is `integer` (but where
    `printed`: `format_type` prints no serial type, so such a name is a type of its own),
    `varchar(20)` is `character varying(20)`, `national char(3)` is `character(3)`,
    `timestamp` is `timestamp without time zone`, `timestamp(6)` the same (six digits is what
    one without a precision keeps), `float(24)` is `real`, `numeric(10)` is `numeric(10,0)`,
    `char` is `character(1)`, `bit` is `bit(1)`, `bpchar(3)` is `character(3)`, `int4[][]` and
    `integer array[3]` are `integer[]` (dimensions are not part of the type: `format_type`
    prints one pair of brackets, and the count `attndims` keeps is not enforced)."""
    text = _blanks(spelling, *_PG_QUOTES)
    written = _outside(text, *_PG_QUOTES, str.lower)
    generic = _PG_GENERIC.fullmatch(text)
    if generic is not None and (generic["schema"] or generic["quoted"] is not None):
        name = generic["quoted"] if generic["quoted"] is not None else generic["bare"].lower()
        qualified = bool(generic["schema"])
        return _pg_generic(name, generic["rest"].lower(), qualified, written, printed)
    body, array_keyword = _pg_array_keyword(written)
    match = _PG_SPELLING.fullmatch(body)
    if match is None:
        return written
    name, modifier, zone, array = match.group("name", "modifier", "zone", "array")
    if name in _PG_SERIAL and not printed:
        bare = modifier is None and not zone and not array and not array_keyword
        return _PG_SERIAL[name] if bare else written
    return _pg_canonical(name, modifier, zone, bool(array) or array_keyword, written)


def _pg_generic(name: str, rest: str, qualified: bool, written: str, printed: bool) -> str:
    """A name the catalog is asked for as written: quoted, or qualified by `pg_catalog`. No
    keyword reads it, so `"char"` is the one-byte `"char"`, a `bit` with no length is the
    unbounded `"bit"`, and a keyword-only name such as `"integer"` names no type."""
    if not _PG_IDENTIFIER.fullmatch(name) or name in _PG_KEYWORD_ONLY:
        return written
    if name in _PG_SERIAL and not printed:
        # quoted, a serial type is one as bare; `pg_catalog.serial` and `"serial"[]` are refused
        return _PG_SERIAL[name] if not qualified and not rest else written
    body, array_keyword = _pg_array_keyword(rest)
    tail = _PG_GENERIC_TAIL.fullmatch(body)
    if tail is None:
        return written
    modifier, array = tail.group("modifier", "array")
    brackets = "[]" if array or array_keyword else ""
    if modifier is not None and name not in _PG_GENERIC_MODIFIED:
        return written
    if name in ("char", "bit") and modifier is None:
        return f'"{name}"{brackets}'
    return _pg_canonical(name, modifier, None, bool(brackets), written)


def _pg_array_keyword(text: str) -> tuple[str, bool]:
    """`text` without the SQL `ARRAY` keyword (and its bound) after the type, and whether it had
    one. The keyword takes no brackets before it, nor a second keyword."""
    head, keyword, bound = text.rpartition(" array")
    if keyword and "[" not in head and _PG_ARRAY_BOUND.fullmatch(bound):
        return head, True
    return text, False


def _pg_canonical(
    name: str, modifier: str | None, zone: str | None, array: bool, written: str
) -> str:
    """The `format_type` name of a type read as a name, a modifier, a zone and brackets. A
    quoted or qualified `char` or `bit` with no modifier never comes here (`_pg_generic`)."""
    name = _PG_ALIASES.get(name, name)
    parts = None if modifier is None else [_pg_number(part.strip()) for part in modifier.split(",")]
    if name == "bpchar" and parts is not None:
        name = "character"  # with no length `bpchar` is unbounded, not `character(1)`
    if name in _PG_ZONED:
        if zone:
            return written  # `timestamptz without time zone` names no type
        name, zone = _PG_ZONED[name], " with time zone"
    if name in ("timestamp", "time"):
        zone = zone or " without time zone"
        if parts is not None and len(parts) == 1 and _pg_digits(parts[0]):
            if len(parts[0]) > len(str(_PG_INT_MAX)) or int(parts[0]) > _PG_INT_MAX:
                return written  # not a number the grammar reads
            if int(parts[0]) >= _PG_TIME_PRECISION:
                parts = None  # as many digits as a time with no precision keeps
    elif name == "float" and (precision := 53 if parts is None else _pg_precision(parts)):
        # `float` is `float8`; `float(p)` is `float4` up to 24 binary digits, `float8` to 53
        name, parts = ("real" if precision <= 24 else "double precision"), None
    elif name == "numeric" and parts is not None and len(parts) == 1:
        parts.append("0")  # a precision with no scale is scale 0
    elif name == "character" and parts is None:
        parts = ["1"]  # SQL: `character` with no length is `character(1)`
    elif name == "bit" and parts is None:
        parts = ["1"]  # SQL: `bit` with no length is `bit(1)`
    typmod = "" if parts is None else f"({','.join(parts)})"
    return f"{name}{typmod}{zone or ''}{'[]' if array else ''}"


def _pg_digits(part: str) -> bool:
    return part.isascii() and part.isdecimal()


def _pg_number(part: str) -> str:
    """A modifier part of digits without the zeros before it: the grammar reads a number."""
    return (part.lstrip("0") or "0") if _pg_digits(part) else part


def _pg_precision(parts: list[str]) -> int | None:
    """The binary precision `float(p)` takes, 1 to 53; `None` for any other modifier."""
    if len(parts) != 1 or not _pg_digits(parts[0]):
        return None
    # `int()` refuses more than 4300 digits, and the model's type is free text
    digits = parts[0].lstrip("0")
    if len(digits) > 2:
        return None
    precision = int(digits or "0")
    return precision if 1 <= precision <= 53 else None


# --- ClickHouse --------------------------------------------------------------------------

_CH_INTEGERS = frozenset(
    f"{sign}Int{bits}" for sign in ("", "U") for bits in (8, 16, 32, 64, 128, 256)
)
_CH_FLOATS = frozenset({"Float32", "Float64"})
#: Families that take no argument.
_CH_PLAIN = frozenset({"Bool", "Date", "Date32", "UUID", "IPv4", "IPv6"})
#: Families that wrap one type.
_CH_WRAPPERS = frozenset({"Nullable", "LowCardinality", "Array"})
#: `DecimalN(S)` is `Decimal(P, S)` of the precision N bits hold.
_CH_DECIMAL_N = {"Decimal32": 9, "Decimal64": 18, "Decimal128": 38, "Decimal256": 76}
_CH_FAMILIES = {
    name.upper(): name
    for name in (
        *_CH_INTEGERS,
        *_CH_FLOATS,
        *_CH_PLAIN,
        *_CH_WRAPPERS,
        *_CH_DECIMAL_N,
        "Map",
        "String",
        "FixedString",
        "Decimal",
        "DateTime",
        "DateTime32",
        "DateTime64",
    )
}
#: `system.data_type_families` of ClickHouse 24.8: alias -> the family it names. Every alias
#: there is case-insensitive. `TIME` is left out: 24.8 reads it as `Int64`, and later versions
#: have a `Time` type of their own. `ENUM` is left out with `Enum`, whose size depends on its
#: values.
_CH_ALIASES = {
    **dict.fromkeys(("BOOL", "BOOLEAN"), "Bool"),
    "TIMESTAMP": "DateTime",
    **dict.fromkeys(("DEC", "FIXED", "NUMERIC"), "Decimal"),
    "BINARY": "FixedString",
    **dict.fromkeys(("FLOAT", "REAL", "SINGLE"), "Float32"),
    **dict.fromkeys(("DOUBLE", "DOUBLE PRECISION"), "Float64"),
    "INET4": "IPv4",
    "INET6": "IPv6",
    **dict.fromkeys(("SMALLINT", "SMALLINT SIGNED"), "Int16"),
    **dict.fromkeys(
        ("INT", "INT SIGNED", "INTEGER", "INTEGER SIGNED", "MEDIUMINT", "MEDIUMINT SIGNED"),
        "Int32",
    ),
    **dict.fromkeys(("BIGINT", "BIGINT SIGNED", "SIGNED"), "Int64"),
    **dict.fromkeys(("BYTE", "INT1", "INT1 SIGNED", "TINYINT", "TINYINT SIGNED"), "Int8"),
    **dict.fromkeys(
        (
            "BINARY LARGE OBJECT",
            "BINARY VARYING",
            "BLOB",
            "BYTEA",
            "CHAR",
            "CHAR LARGE OBJECT",
            "CHAR VARYING",
            "CHARACTER",
            "CHARACTER LARGE OBJECT",
            "CHARACTER VARYING",
            "CLOB",
            "GEOMETRY",
            "LONGBLOB",
            "LONGTEXT",
            "MEDIUMBLOB",
            "MEDIUMTEXT",
            "NATIONAL CHAR",
            "NATIONAL CHAR VARYING",
            "NATIONAL CHARACTER",
            "NATIONAL CHARACTER LARGE OBJECT",
            "NATIONAL CHARACTER VARYING",
            "NCHAR",
            "NCHAR LARGE OBJECT",
            "NCHAR VARYING",
            "NVARCHAR",
            "TEXT",
            "TINYBLOB",
            "TINYTEXT",
            "VARBINARY",
            "VARCHAR",
            "VARCHAR2",
        ),
        "String",
    ),
    **dict.fromkeys(("SMALLINT UNSIGNED", "YEAR"), "UInt16"),
    **dict.fromkeys(("INT UNSIGNED", "INTEGER UNSIGNED", "MEDIUMINT UNSIGNED"), "UInt32"),
    **dict.fromkeys(("BIGINT UNSIGNED", "BIT", "SET", "UNSIGNED"), "UInt64"),
    **dict.fromkeys(("INT1 UNSIGNED", "TINYINT UNSIGNED"), "UInt8"),
}
#: The only word a type may take after its arguments, as in `INT(11) UNSIGNED`.
_CH_TRAILING = frozenset({"SIGNED", "UNSIGNED"})
#: Digits of a number argument the engine is sure to read (a longer one may not fit `UInt64`).
_CH_MAX_DIGITS = 19
#: The longest `FixedString` the engine creates without a setting that allows more.
_CH_FIXED_MAX = 256
#: How deep types nest before the spelling is left as written.
_CH_MAX_DEPTH = 32

#: One token after single blanks: a word, a number, a string without escapes, a mark.
_CH_TOKEN = re.compile(
    r" ?(?:(?P<word>[A-Za-z_][A-Za-z0-9_]*)|(?P<number>-?[0-9]+)"
    r"|(?P<string>'[^'\\]*')|(?P<mark>[(),]))"
)


class _Literal(NamedTuple):
    kind: str  # "number" or "string"
    text: str


class _Type(NamedTuple):
    words: tuple[str, ...]
    args: tuple[_Literal | _Type, ...] | None
    trailing: tuple[str, ...]


class _Unread(Exception):
    """The spelling is not one the rules read."""


def _ch_type(spelling: str) -> str:
    """A ClickHouse type as `system.columns` prints it, from the spellings of it the rules read:
    `BIGINT` is `Int64`, `INT(11) UNSIGNED` is `UInt32`, `VARCHAR(255)` is `String`,
    `DOUBLE` is `Float64`, `DECIMAL` is `Decimal(10, 0)`, `Decimal64(4)` is `Decimal(18, 4)`,
    `TIMESTAMP(3)` is `DateTime64(3)`, `Nullable(BIGINT)` is `Nullable(Int64)`. A family name
    is read in any case, as the comparison read every name before D1."""
    text = _blanks(spelling, *_CH_QUOTES)
    try:
        tokens = _ch_tokens(text)
        node, end = _ch_parse(tokens, 0, 0)
        if end != len(tokens) or not isinstance(node, _Type):
            raise _Unread
        return _ch_render(node)
    except _Unread:
        return text


def _ch_tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _CH_TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise _Unread
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match[kind]))
        position = match.end()
    return tokens


def _ch_parse(tokens: list[tuple[str, str]], at: int, depth: int) -> tuple[_Literal | _Type, int]:
    """One argument from token `at`: a literal, or a type — words, arguments, a trailing word."""
    if depth > _CH_MAX_DEPTH or at >= len(tokens):
        raise _Unread
    kind, text = tokens[at]
    if kind in ("number", "string"):
        return _Literal(kind, text), at + 1
    words: list[str] = []
    while at < len(tokens) and tokens[at][0] == "word":
        words.append(tokens[at][1])
        at += 1
    if not words:
        raise _Unread
    args: list[_Literal | _Type] | None = None
    if at < len(tokens) and tokens[at] == ("mark", "("):
        args, at = [], at + 1
        if at < len(tokens) and tokens[at] == ("mark", ")"):
            at += 1  # empty parentheses: `INT()` is `Int32`, `DECIMAL()` is refused
        else:
            at = _ch_arguments(tokens, at, depth, args)
    trailing: list[str] = []
    while at < len(tokens) and tokens[at][0] == "word":
        trailing.append(tokens[at][1])
        at += 1
    return _Type(tuple(words), None if args is None else tuple(args), tuple(trailing)), at


def _ch_arguments(
    tokens: list[tuple[str, str]], at: int, depth: int, args: list[_Literal | _Type]
) -> int:
    """The arguments from token `at` to the closing parenthesis, into `args`; the token after."""
    while True:
        arg, at = _ch_parse(tokens, at, depth + 1)
        args.append(arg)
        mark = tokens[at] if at < len(tokens) else None
        if mark == ("mark", ")"):
            return at + 1
        if mark != ("mark", ","):
            raise _Unread
        at += 1


def _ch_render(node: _Type) -> str:
    trailing = tuple(word.upper() for word in node.trailing)
    if trailing and (len(trailing) != 1 or trailing[0] not in _CH_TRAILING):
        raise _Unread
    name = " ".join(node.words + trailing).upper()
    family = _CH_ALIASES.get(name) or _CH_FAMILIES.get(name)
    if family is None:  # a trailing word is part of the name: only integers have one
        raise _Unread
    args = node.args
    if family in _CH_INTEGERS or family == "String":
        # a display width, as MySQL writes it: the engine takes it and keeps none
        if not args or (len(args) == 1 and _ch_unsigned(args[0]) is not None):
            return family
        raise _Unread
    if family in _CH_FLOATS:
        if args is None or (len(args) <= 2 and all(_ch_signed(arg) for arg in args)):
            return family
        raise _Unread
    if family in _CH_PLAIN:
        if args is None:
            return family
        raise _Unread
    if family in _CH_WRAPPERS:
        if args is not None and len(args) == 1 and isinstance(args[0], _Type):
            return f"{family}({_ch_render(args[0])})"
        raise _Unread
    if family == "Map":
        if args is not None and len(args) == 2 and all(isinstance(arg, _Type) for arg in args):
            return (
                "Map(" + ", ".join(_ch_render(arg) for arg in args if isinstance(arg, _Type)) + ")"
            )
        raise _Unread
    return _ch_parametric(family, args)


def _ch_parametric(family: str, args: tuple[_Literal | _Type, ...] | None) -> str:
    """The families whose arguments the engine prints: fixed strings, decimals, date-times."""
    values = [] if args is None else list(args)
    if family == "FixedString":
        size = _ch_small(values[0], _CH_FIXED_MAX) if len(values) == 1 else None
        if size is not None and size >= 1:
            return f"FixedString({size})"
        raise _Unread
    if family == "Decimal" and args != () and len(values) <= 2:
        # no arguments is `Decimal(10, 0)`, a precision alone is scale 0; `DECIMAL()` is refused
        numbers = [_ch_small(value, 76) for value in values] + [10, 0][len(values) :]
        precision, scale = numbers
        if precision is not None and scale is not None and precision >= 1 and scale <= precision:
            return f"Decimal({precision}, {scale})"
        raise _Unread
    if family in _CH_DECIMAL_N and len(values) == 1:
        bits_precision = _CH_DECIMAL_N[family]
        scale = _ch_small(values[0], bits_precision)
        if scale is not None:
            return f"Decimal({bits_precision}, {scale})"
        raise _Unread
    if family in ("DateTime", "DateTime32", "DateTime64"):
        return _ch_datetime(family, values)
    raise _Unread


def _ch_datetime(family: str, values: list[_Literal | _Type]) -> str:
    """`DateTime` and `TIMESTAMP` with a precision are `DateTime64` of it, but for precision 0;
    `DateTime64` with none is precision 3; a zone, a string, comes last."""
    zone = None
    last = values[-1] if values else None
    if isinstance(last, _Literal) and last.kind == "string":
        zone = last.text
        values.pop()
    if len(values) > 1 or (family == "DateTime32" and values):
        raise _Unread
    precision = _ch_small(values[0], 9) if values else (3 if family == "DateTime64" else 0)
    if precision is None or (zone is not None and values == [] and family == "DateTime64"):
        raise _Unread
    zoned = "" if zone is None else f", {zone}"
    if precision == 0 and family != "DateTime64":
        return "DateTime" if zone is None else f"DateTime({zone})"
    return f"DateTime64({precision}{zoned})"


def _ch_unsigned(arg: _Literal | _Type) -> str | None:
    """The digits of an unsigned number argument the engine is sure to read, zeros stripped."""
    if not isinstance(arg, _Literal) or arg.kind != "number" or arg.text.startswith("-"):
        return None
    digits = arg.text.lstrip("0") or "0"
    return digits if len(digits) <= _CH_MAX_DIGITS else None


def _ch_signed(arg: _Literal | _Type) -> bool:
    if not isinstance(arg, _Literal) or arg.kind != "number":
        return False
    return _ch_unsigned(_Literal("number", arg.text.removeprefix("-"))) is not None


def _ch_small(arg: _Literal | _Type, limit: int) -> int | None:
    """An unsigned number argument up to `limit`; `None` for anything else."""
    digits = _ch_unsigned(arg)
    if digits is None or len(digits) > len(str(limit)):
        return None
    value = int(digits)
    return value if value <= limit else None
