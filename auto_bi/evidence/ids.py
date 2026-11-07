"""Ids of bundle records derived from a ref (DECISIONOPS_ARCHITECTURE §9.2)."""

from __future__ import annotations

from auto_bi.evidence.canonical import sha256_digest


def ref_id(kind: str, ref: str, *parts: str) -> str:
    """The id of an artifact or claim about *ref*: a slug to read, then the hash it is told by.

    The slug folds `_`, `-`, `/`, `:` and `@` alike, so `asset://sales/weekly_margin_by_region`
    and `asset://sales/weekly-margin-by-region` share one, and a bundle holds an id once. The
    full SHA-256 of the canonical array `[ref, *parts]` keeps every segment and separator, the
    pin and the boundary between the ref and its parts; it does not depend on the payload or on
    any other ref of the run.
    """
    identity = sha256_digest([ref, *parts]).removeprefix("sha256:")
    return "-".join([kind, slugify(ref), *parts, identity])


def slugify(ref: str) -> str:
    """A ref (`decision://sales/weekly_margin@1`) as the readable part of an id; lossy.

    Only ASCII letters and digits are kept, lowered: a letter or digit outside ASCII — a dbt
    metric name may hold one — is no character of an id, and is folded like any other, before
    lowering could make an ASCII letter of it (`K`, the Kelvin sign, lowers to `k`).
    """
    out = "".join(char.lower() if char.isascii() and char.isalnum() else "-" for char in ref)
    return "-".join(part for part in out.split("-") if part) or "ref"


__all__ = ["ref_id", "slugify"]
