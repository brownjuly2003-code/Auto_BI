"""Shape the Public role for anonymous dashboard VIEWING (P8 demo bootstrap).

PUBLIC_ROLE_LIKE="Gamma" copies Gamma's UI permissions but deliberately NOT data
access. Auto_BI creates its datasets at build time, so per-dataset grants cannot
exist at init — `all_datasource_access` is the only grant that covers them. Fine
here: the only data in the container is the synthetic read-only demo DM.

Gamma can also CREATE charts/dashboards — and any permission the Public role
holds is "public" to FAB, which then runs the request WITHOUT verifying the JWT
at all (flask_appbuilder protect(): is_item_public short-circuits first). That
turned the adapter's authenticated POST /api/v1/chart/ into an anonymous request
and crashed the owners flush ('AnonymousUserMixin' has no '_sa_instance_state').
So every write-like permission is stripped: the Public role must be strictly
read-only, both for security and for authenticated writes to keep working.

The same short-circuit poisons READS, silently: with Gamma's `can_read on
Database` on Public, the adapter's authenticated GET /api/v1/database/ also ran
as anonymous, and DatabaseFilter hides every connection from anonymous (
`all_datasource_access` covers datasets, not Database rows) — so ensure_database
never found the existing connection and every build after the container's first
died on POST 422 "A database with the same name already exists". Anonymous
viewers never list connections, so `can_read on Database` is stripped too;
dataset/chart/dashboard reads stay (anonymous lookups there DO see the rows:
datasets via all_datasource_access, dashboards because the adapter publishes).
"""

import re

from superset.app import create_app

# write-like FAB permission names (REST can_write/can_post..., MVC can_add/can_edit...,
# bulk ops, imports/exports, favourites) — nothing an anonymous viewer needs
_WRITE_RE = re.compile(
    r"^(can_(write|post|put|add|edit|delete|save|copy|import|export|create|update"
    r"|upload|fave|favorite|bulk|invalidate|cache|warm)|mul|muldelete)"
)

app = create_app()
with app.app_context():
    from superset.extensions import security_manager as sm

    role = sm.find_role("Public")
    assert role is not None, "superset init must run before this script"

    stripped = 0
    for pv in list(role.permissions):
        if pv.permission is not None and _WRITE_RE.match(pv.permission.name or ""):
            role.permissions.remove(pv)
            stripped += 1

    # reads the adapter depends on must NOT be public (see module docstring): a
    # Public-held permission makes FAB run even authenticated calls as anonymous,
    # and anonymous DatabaseFilter hides the DWH connection from ensure_database
    db_read = sm.find_permission_view_menu("can_read", "Database")
    if db_read is not None and db_read in role.permissions:
        role.permissions.remove(db_read)
        stripped += 1

    pv = sm.find_permission_view_menu("all_datasource_access", "all_datasource_access")
    assert pv is not None
    if pv not in role.permissions:
        role.permissions.append(pv)

    sm.get_session.commit()
    print(
        f"Public role: {len(role.permissions)} permissions "
        f"({stripped} write-like stripped, datasource access granted)"
    )
