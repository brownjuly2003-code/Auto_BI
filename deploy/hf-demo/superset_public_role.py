"""Grant the Public role blanket datasource access (P8 demo bootstrap).

PUBLIC_ROLE_LIKE="Gamma" copies Gamma's UI permissions but deliberately NOT data
access. Auto_BI creates its datasets at build time, so per-dataset grants cannot
exist at init — `all_datasource_access` is the only grant that covers them. Fine
here: the only data in the container is the synthetic read-only demo DM.
"""

from superset.app import create_app

app = create_app()
with app.app_context():
    from superset.extensions import security_manager as sm

    role = sm.find_role("Public")
    assert role is not None, "superset init must run before this script"
    pv = sm.find_permission_view_menu("all_datasource_access", "all_datasource_access")
    assert pv is not None
    if pv not in role.permissions:
        role.permissions.append(pv)
        sm.get_session.commit()
    print(f"Public role permissions: {len(role.permissions)} (datasource access granted)")
