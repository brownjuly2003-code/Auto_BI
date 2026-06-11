# Superset version is PINNED (CLAUDE.md invariant 7): upgrading is a separate task
# that must re-run the form_data contract tests.
FROM apache/superset:4.1.2

USER root
RUN pip install --no-cache-dir "clickhouse-connect>=0.8,<1"

COPY entrypoint.sh /app/entrypoint-auto-bi.sh
RUN chmod +x /app/entrypoint-auto-bi.sh

USER superset
ENTRYPOINT ["/app/entrypoint-auto-bi.sh"]
