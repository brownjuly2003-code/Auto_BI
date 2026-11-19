"""E4a stand config, baked into both Superset images as /app/pythonpath/superset_config.py.

The examples go to the stand's Postgres (one database per Superset version), whose statement log is
the reference of what the engine ran. Jinja templating is on, so the research can probe it.
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_EXAMPLES_URI = os.environ["E4A_EXAMPLES_URI"]
FEATURE_FLAGS = {"ENABLE_TEMPLATE_PROCESSING": True}
# The metadata DB is the image's SQLite: a dashboard opening a dozen charts reads while the
# event log writes, and SQLite's 5 s busy wait ran out (500 "database is locked" on 6.1).
SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
