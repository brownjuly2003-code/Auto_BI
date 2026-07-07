"""Superset config for the public Auto_BI demo (P8).

Anonymous viewers must open the dashboards Auto_BI builds (published=True by the
adapter): PUBLIC_ROLE_LIKE bootstraps the Public role from Gamma, and
superset_public_role.py grants it datasource access after `superset init`
(datasets are created later, at build time — a blanket grant is the only option).

The whole stack is a synthetic, ephemeral, read-only playground: the metadata DB
is SQLite on container-local disk and the SECRET_KEY is generated per start —
nothing here outlives a restart on purpose.
"""

import os
import secrets

SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY") or secrets.token_hex(32)

PUBLIC_ROLE_LIKE = "Gamma"

# behind the in-container nginx (and HF's own proxy in front of that)
ENABLE_PROXY_FIX = True

# the demo iframe on huggingface.co embeds the direct Space URL
TALISMAN_ENABLED = False
