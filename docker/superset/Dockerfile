# Superset version is PINNED (CLAUDE.md invariant 7): upgrading is a separate task
# that must re-run the form_data contract tests.
# C-4 / plan_sol step 6: base by digest (same pin as deploy/hf-demo) + exact
# clickhouse-connect (matches uv.lock app resolution). Bump tag+digest+pin together.
FROM apache/superset:4.1.2@sha256:15e110b8533d3cb6a0d529512ea71252b0ac62e3f72b1f7a5000f1361822ac26

USER root
RUN pip install --no-cache-dir "clickhouse-connect==1.5.0"

COPY entrypoint.sh /app/entrypoint-auto-bi.sh
RUN chmod +x /app/entrypoint-auto-bi.sh

USER superset
ENTRYPOINT ["/app/entrypoint-auto-bi.sh"]
