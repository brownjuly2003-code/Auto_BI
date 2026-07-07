# Public demo image (P8): ClickHouse + Superset + Auto_BI + nginx in ONE container —
# Hugging Face Spaces expose exactly one port (7860), so nginx routes:
#   /agent/*  -> auto_bi :8200 (prefix stripped; the UI uses relative paths)
#   /* (rest) -> Superset :8088 (its /superset/*, /static/assets, /api/v1/* need the root)
# Build context = the repository root:  docker build -f deploy/hf-demo/Dockerfile .
#
# Base: the SAME pinned Superset the contract tests run against (invariant 7).
# auto_bi needs Python >=3.12 while Superset 4.1.2 ships 3.10 — uv installs a managed
# 3.12 into an isolated venv, the two runtimes never mix.
FROM apache/superset:4.1.2

USER root

# --- system deps + ClickHouse server (same major as the stand: 24.8) ---------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg supervisor nginx \
    && curl -fsSL https://packages.clickhouse.com/gpg/KEY.gpg \
        | gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg] https://packages.clickhouse.com/deb stable main" \
        > /etc/apt/sources.list.d/clickhouse.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        clickhouse-server=24.8.* clickhouse-client=24.8.* clickhouse-common-static=24.8.* \
    && rm -rf /var/lib/apt/lists/*

# Superset talks to ClickHouse through clickhouse-connect (mirrors docker/superset)
RUN pip install --no-cache-dir "clickhouse-connect>=0.8,<1"

# --- auto_bi in its own Python 3.12 venv (uv-managed interpreter) -------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md LICENSE /opt/autobi-src/
COPY auto_bi /opt/autobi-src/auto_bi
RUN uv venv --python 3.12 /opt/autobi \
    && uv pip install --python /opt/autobi/bin/python /opt/autobi-src \
    && /opt/autobi/bin/auto_bi --version

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
        /tmp/auto_bi \
    && chown -R superset:superset \
        /var/lib/clickhouse /var/log/clickhouse-server /etc/clickhouse-server \
        /app/superset_home /opt/demo

ENV SUPERSET_CONFIG_PATH=/opt/demo/superset_config.py \
    SUPERSET_HOME=/app/superset_home \
    DEMO_FACT_ROWS=1000000

USER superset
EXPOSE 7860

CMD ["supervisord", "-c", "/opt/demo/supervisord.conf"]
