"""Superset config for the public Auto_BI demo (P8).

Anonymous viewers must open the dashboards Auto_BI builds (published=True by the
adapter): PUBLIC_ROLE_LIKE bootstraps the Public role from Gamma, and
superset_public_role.py grants it datasource access after `superset init`
(datasets are created later, at build time — a blanket grant is the only option).

The whole stack is a synthetic, ephemeral, read-only playground: the metadata DB
is SQLite on container-local disk — nothing here outlives a restart on purpose.

SECRET_KEY comes strictly from the environment (start-superset.sh generates one
per container): a per-import fallback would give every gunicorn worker its OWN
key, so a session/JWT signed by one worker reads as anonymous on another — and
with the Gamma-like Public role that anonymous request got far enough to crash
chart creation ('AnonymousUserMixin' has no '_sa_instance_state').
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

PUBLIC_ROLE_LIKE = "Gamma"

# behind the in-container nginx (and HF's own proxy in front of that)
ENABLE_PROXY_FIX = True

# the demo iframe on huggingface.co embeds the direct Space URL
TALISMAN_ENABLED = False
