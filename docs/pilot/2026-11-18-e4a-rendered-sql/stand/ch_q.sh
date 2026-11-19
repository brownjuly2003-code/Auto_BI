#!/bin/bash
# Run one ClickHouse query on the stand as admin; the query is the first argument (no secrets printed).
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export DOCKER_CONTEXT=colima-autobi-linux
docker exec e4a_clickhouse sh -c 'clickhouse-client --user admin --password "$CLICKHOUSE_PASSWORD" --multiquery --query "$0"' "$1"
