"""Build-scoped artifact identity (audit P0-2).

Human dashboard/chart titles are display metadata only. Technical BI names for
datasets (and DataLens widgets/dashboards) must include a short non-secret
fingerprint of a build/session namespace so two independent sessions with the
same title/chart ids never share or overwrite each other's BI artifacts.

The BIAdapter Protocol is unchanged (CLAUDE.md S4): callers set the namespace on
the concrete adapter via `set_artifact_namespace` before `build()`.
"""

from __future__ import annotations

import hashlib
import re
import uuid


def new_build_namespace(session_id: str | None = None) -> str:
    """Stable-enough, non-secret namespace for one build.

    Prefer the durable session id when present (rebuilds of the same dialogue share
    a family of names for ops readability). Always append a short random token so
    two concurrent builds of the same session still never collide, and so a rebuild
    never PUTs over a dataset still referenced by a previous dashboard.
    """
    base = (session_id or "local").strip() or "local"
    return f"{base}:{uuid.uuid4().hex[:8]}"


def namespace_fingerprint(namespace: str, *, length: int = 8) -> str:
    """Short non-secret hex fingerprint of a namespace (empty -> empty)."""
    ns = (namespace or "").strip()
    if not ns:
        return ""
    return hashlib.sha1(ns.encode("utf-8")).hexdigest()[:length]


def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_")[:max_len] or "dataset"


def dataset_table_name(title: str, chart_id: str, namespace: str = "") -> str:
    """Superset table_name / DataLens dataset entry name.

    Hash covers chart_id + namespace so equal titles across sessions never collide.
    When namespace is empty the historical layout is preserved (single-user / unit
    tests that pass names explicitly).
    """
    ns_fp = namespace_fingerprint(namespace, length=6)
    key = f"{chart_id}\0{namespace}" if namespace else chart_id
    suffix = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    if ns_fp:
        return f"auto_bi__{_slug(title)}__{_slug(chart_id)}__{ns_fp}__{suffix}"
    return f"auto_bi__{_slug(title)}__{_slug(chart_id)}__{suffix}"
