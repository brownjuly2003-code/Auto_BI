# Public demo image (P8): ClickHouse + Superset + Auto_BI + nginx in ONE container —
# Hugging Face Spaces expose exactly one port (7860), so nginx routes:
#   /agent/*  -> auto_bi :8200 (prefix stripped; the UI uses relative paths)
#   /* (rest) -> Superset :8088 (its /superset/*, /static/assets, /api/v1/* need the root)
# Build context = the repository root:  docker build -f deploy/hf-demo/Dockerfile .
#
# Base: the SAME pinned Superset the contract tests run against (invariant 7).
# auto_bi needs Python >=3.12 while Superset 4.1.2 ships 3.10 — uv installs a managed
# 3.12 into an isolated venv, the two runtimes never mix.
# ClickHouse comes from ITS pinned official image (same 24.8 as the compose stand) —
# one static binary; apt/packages.clickhouse.com is not reachable from every network.
FROM clickhouse/clickhouse-server:24.8 AS clickhouse

FROM apache/superset:4.1.2

USER root

# --- system deps -------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates supervisor nginx \
    && rm -rf /var/lib/apt/lists/*

# --- ClickHouse server: the single static binary + stock configs from the image -----
COPY --from=clickhouse /usr/bin/clickhouse /usr/bin/clickhouse
COPY --from=clickhouse /etc/clickhouse-server /etc/clickhouse-server
RUN ln -s /usr/bin/clickhouse /usr/bin/clickhouse-server \
    && ln -s /usr/bin/clickhouse /usr/bin/clickhouse-client

# Superset talks to ClickHouse through clickhouse-connect (mirrors docker/superset)
RUN pip install --no-cache-dir "clickhouse-connect>=0.8,<1"

# --- auto_bi in its own Python 3.12 venv (uv-managed interpreter) -------------------
# The managed interpreter must live on a world-readable path: the build runs as root
# but the container runs as UID 1000 — uv's default ~/.local/... under /root is 0700,
# which made the venv shebang unlaunchable (exit 126).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
COPY pyproject.toml README.md LICENSE /opt/autobi-src/
COPY auto_bi /opt/autobi-src/auto_bi
RUN uv venv --python 3.12 /opt/autobi \
    && uv pip install --python /opt/autobi/bin/python /opt/autobi-src \
    && /opt/autobi/bin/auto_bi --version \
    && chmod -R a+rX /opt/uv-python /opt/autobi

# --- demo assets: DM init scripts, semantic model, service configs ------------------
COPY docker/clickhouse/initdb /opt/demo/initdb
COPY semantic/model.yaml /opt/demo/semantic/model.yaml
COPY deploy/hf-demo/nginx.conf /opt/demo/nginx.conf
COPY deploy/hf-demo/supervisord.conf /opt/demo/supervisord.conf
COPY deploy/hf-demo/superset_config.py /opt/demo/superset_config.py
COPY deploy/hf-demo/superset_public_role.py /opt/demo/superset_public_role.py
COPY deploy/hf-demo/start-superset.sh deploy/hf-demo/start-autobi.sh /opt/demo/
RUN chmod +x /opt/demo/start-*.sh

# --- non-root (HF Spaces run as UID 1000 = the image's `superset` user) --------------
# ClickHouse/nginx/supervisor all write under paths owned by that user; nginx temp/pid
# live in /tmp (see nginx.conf).
RUN mkdir -p /var/lib/clickhouse /var/log/clickhouse-server /app/superset_home \
    && chown -R superset:superset \
        /var/lib/clickhouse /var/log/clickhouse-server /etc/clickhouse-server \
        /app/superset_home /opt/demo

ENV SUPERSET_CONFIG_PATH=/opt/demo/superset_config.py \
    SUPERSET_HOME=/app/superset_home \
    DEMO_FACT_ROWS=1000000

USER superset
EXPOSE 7860

CMD ["supervisord", "-c", "/opt/demo/supervisord.conf"]
