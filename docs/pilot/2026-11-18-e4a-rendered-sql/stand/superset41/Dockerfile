# E4a stand: the project's pinned Superset 4.1.2 image (docker/superset/Dockerfile, same base digest
# and clickhouse-connect pin) plus psycopg2-binary, the driver of the stand's Postgres examples DB.
FROM apache/superset:4.1.2@sha256:15e110b8533d3cb6a0d529512ea71252b0ac62e3f72b1f7a5000f1361822ac26

USER root
RUN pip install --no-cache-dir "clickhouse-connect==1.6.0"
RUN pip install --no-cache-dir "psycopg2-binary==2.9.10"

COPY entrypoint.sh /app/entrypoint-auto-bi.sh
COPY superset_config.py /app/pythonpath/superset_config.py
RUN chmod +x /app/entrypoint-auto-bi.sh

USER superset
ENTRYPOINT ["/app/entrypoint-auto-bi.sh"]
