# E4a stand: ClickHouse 24.8 with the repo's demo-DM init (docker/clickhouse/initdb) and the
# stand-only dm.sales_wide, baked in (the colima VM has no host mounts).
FROM clickhouse/clickhouse-server:24.8@sha256:1ffa82edee000a42c09313bd9f1293d94c570aee74babc1b3ca9983a35fa597b
COPY initdb/ /docker-entrypoint-initdb.d/
