# E4a stand: Superset 6.1.0 (the lean image has no database drivers) with the same ClickHouse
# driver pin as the project's 4.1.2 image, plus psycopg2-binary for the Postgres examples DB.
FROM apache/superset:6.1.0@sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88

USER root
RUN uv pip install --no-cache --python /app/.venv/bin/python "clickhouse-connect==1.6.0" "psycopg2-binary==2.9.10"

COPY entrypoint.sh /app/entrypoint-auto-bi.sh
COPY superset_config.py /app/pythonpath/superset_config.py
RUN chmod +x /app/entrypoint-auto-bi.sh

USER superset
ENTRYPOINT ["/app/entrypoint-auto-bi.sh"]
