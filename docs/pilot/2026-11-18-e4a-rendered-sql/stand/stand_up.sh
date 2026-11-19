#!/bin/bash
# E4a stand on the Mac: bring up ClickHouse + Postgres + ONE Superset (41 = 4.1.2 on :8088,
# 61 = 6.1.0 on :8089), load the examples once per version, then switch the engine statement log on
# for that version's examples database. Secrets are generated into $S/.env (0600), never printed.
# Usage: bash ~/e4astand/stand_up.sh 41|61
set -u
V="${1:?41 or 61}"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_CONTEXT=colima-autobi-linux
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S" || exit 9
if [ ! -f "$S/.env" ]; then
  (
    umask 077
    {
      echo "SUPERSET_SECRET_KEY=$(openssl rand -hex 32)"
      echo "AUTO_BI_SUPERSET_USER=admin"
      echo "AUTO_BI_SUPERSET_PASSWORD=$(openssl rand -hex 16)"
      echo "INVENTORY_SUPERSET_USER=inventory_reader"
      echo "INVENTORY_SUPERSET_PASSWORD=$(openssl rand -hex 16)"
      echo "CH_ADMIN_PASSWORD=$(openssl rand -hex 16)"
      echo "AUTO_BI_CH_PASSWORD=$(openssl rand -hex 16)"
      echo "PG_EXAMPLES_PASSWORD=$(openssl rand -hex 16)"
      echo "DEMO_FACT_ROWS=20000"
    } > "$S/.env"
  )
fi
chmod 600 "$S/.env"
mkdir -p "$S/logs"
colima status autobi-linux >/dev/null 2>&1 || colima start autobi-linux 2>&1 | tail -2
# One Superset at a time: stop the other version's container if it runs.
other=$([ "$V" = 41 ] && echo 61 || echo 41)
docker stop "e4a_superset$other" >/dev/null 2>&1 && echo "stopped e4a_superset$other"
echo "== compose up s$V"
docker compose -p e4a --env-file .env --profile "s$V" up -d --build 2>&1 | tail -4
port=$([ "$V" = 41 ] && echo 8088 || echo 8089)
for i in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$port/health" >/dev/null && break
  sleep 10
done
curl -sf "http://127.0.0.1:$port/health" >/dev/null && echo "health ok :$port" || { echo "HEALTH_FAIL"; docker logs --tail 30 "e4a_superset$V" 2>&1 | grep -v -i password; exit 3; }
docker exec "e4a_superset$V" sh -c 'python -c "import importlib.metadata as m;print(\"apache-superset\", m.version(\"apache-superset\"))"'
if [ ! -f "$S/examples$V.done" ]; then
  echo "== load-examples s$V"
  docker exec "e4a_superset$V" superset load-examples > "$S/logs/load_examples_$V.log" 2>&1
  echo "LOAD_EXIT=$?"
  grep -v -i password "$S/logs/load_examples_$V.log" | tail -8
  docker exec e4a_postgres psql -U examples -d postgres -v ON_ERROR_STOP=1 \
    -c "ALTER DATABASE examples_$V SET log_statement = 'all'" && touch "$S/examples$V.done"
fi
docker ps --format '{{.Names}} {{.Status}}'
