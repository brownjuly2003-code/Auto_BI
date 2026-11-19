"""Inside the Superset container: the viewer of the user probe — user from env with Gamma and the
role `inventory_reader`, which holds `datasource_access` on the C2 datasets
(database `e4a ClickHouse`).

Gamma lets the user open dashboards and Explore like a viewer; the role is what an RLS rule names.
Prints names only, never the password.
Usage: docker exec -i -e U=... -e PW=... e4a_superset41 python - < reader_role.py
"""

import os

from superset.app import create_app

app = create_app()
with app.app_context():
    from superset import db
    from superset import security_manager as sm
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database

    role = sm.find_role("inventory_reader") or sm.add_role("inventory_reader")
    database = db.session.query(Database).filter_by(database_name="e4a ClickHouse").one()
    granted = []
    for table in db.session.query(SqlaTable).filter_by(database_id=database.id):
        pv = sm.add_permission_view_menu("datasource_access", table.get_perm())
        sm.add_permission_role(role, pv)
        granted.append(table.table_name)
    gamma = sm.find_role("Gamma")
    user = sm.find_user(username=os.environ["U"])
    if user is None:
        user = sm.add_user(
            os.environ["U"],
            "Inventory",
            "Reader",
            "inventory@localhost",
            [gamma, role],
            password=os.environ["PW"],
        )
    else:
        user.roles = [gamma, role]
        sm.reset_password(user.id, os.environ["PW"])
    db.session.commit()
    print("granted datasource_access:", sorted(granted))
    print("user", user.username, "roles", sorted(r.name for r in user.roles), "role id", role.id)
