#!/bin/bash
# Prueba aislada de empresas (multiempresa de plataforma).
# PostgreSQL y API descartables, sin red; datos sintéticos. No toca producción.
# Uso: docs/run-empresas-tests.sh [rama_base]   (por defecto: master)
# Ver docs/multiempresa-plataforma.md.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
H=$REPO/api/tests/empresas
NEW=$REPO/api
BASE_REF=${1:-master}
MASTER=$(mktemp -d); git -C "$REPO" archive "$BASE_REF" api | tar -x -C "$MASTER"
# Imagen de la API para ejecutar (solo lectura; se monta el código encima).
IMG=${IOT_TEST_API_IMAGE:-$(docker inspect iot-platform-api --format '{{.Image}}')}
SFX=$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')
PG=iot-empresas-test-pg-$SFX; API=iot-empresas-test-api-$SFX
DSN=postgresql://postgres@127.0.0.1:5432/iot_empresas_test
COMMON=(--network container:$PG --read-only --tmpfs /tmp --memory 512m --pids-limit 256
  -e EMPRESAS_TEST_DISPOSABLE=1 -e DATABASE_URL=$DSN -e JWT_SECRET=solo-prueba-$SFX
  -e API_ADMIN_USER=sa-test -e API_ADMIN_PASSWORD=Prueba-SA-1234 -e REDIS_URL=redis://127.0.0.1:6399/0
  -e ACCESS_INTEGRATION_API_KEY=clave-prueba-$SFX -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app)
cleanup(){ docker rm -f $API $PG >/dev/null 2>&1; rm -rf "$MASTER"; }
trap cleanup EXIT
rc=0
docker run -d --name $PG --network none --memory 512m --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=iot_empresas_test postgres:15 >/dev/null
for i in $(seq 30); do docker exec $PG pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 1; done
echo "== 1. Esquema como producción (master) + datos sintéticos"
docker run --rm "${COMMON[@]}" -v "$MASTER/api":/app:ro -v "$H":/h:ro -w /app --entrypoint python $IMG /h/baseline.py || rc=1
docker run --rm "${COMMON[@]}" -v "$MASTER/api":/app:ro -w /app --entrypoint python $IMG -m alembic stamp 20261104_01 2>&1 | tail -1
echo "== 2. Migración nueva"
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic heads 2>&1 | tail -1
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic upgrade head 2>&1 | tail -2
echo "== 2b. Migración idempotente (segunda corrida no rompe)"
docker exec $PG psql -U postgres -d iot_empresas_test -qc "update alembic_version set version_num='20261104_01'"
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic upgrade head 2>&1 | tail -1 || rc=1
docker exec $PG psql -U postgres -d iot_empresas_test -Atc "select count(*) from companies" | sed 's/^/companies=/'
docker run --rm "${COMMON[@]}" -v "$H":/h:ro --entrypoint python $IMG /h/verify_schema.py || rc=1
echo "== 3. API nueva (API_STARTUP_READ_ONLY=true, como producción)"
docker run -d --name $API "${COMMON[@]}" -e API_STARTUP_READ_ONLY=true -v "$NEW":/app:ro -w /app --entrypoint uvicorn $IMG app.main:app --host 127.0.0.1 --port 18999 >/dev/null
for i in $(seq 30); do docker run --rm --network container:$PG $IMG python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:18999/api/health',timeout=2)" >/dev/null 2>&1 && break; sleep 1; done
echo "== 4. Pruebas de empresas"
docker run --rm "${COMMON[@]}" -e IOT_TEST_URL=http://127.0.0.1:18999 -e IOT_TEST_KEY=clave-prueba-$SFX -v "$H":/h:ro --entrypoint python $IMG /h/test_empresas.py 2>&1 | grep -E "^(test_|FAIL|ERROR|AssertionError|Ran|OK|  File \"/h)" || rc=1
echo "== 5. Pruebas existentes de la integración (live_access_integration)"
docker run --rm "${COMMON[@]}" -e IOT_TEST_URL=http://127.0.0.1:18999 -e IOT_TEST_KEY=clave-prueba-$SFX -e IOT_TEST_DSN=$DSN -v "$NEW":/app:ro -w /app/tests --entrypoint python $IMG live_access_integration.py 2>&1 | tail -4
echo "== 6. Pruebas unitarias existentes"
docker run --rm --network none --read-only --tmpfs /tmp -v "$NEW":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python $IMG -m unittest tests.test_startup 2>&1 | tail -2
echo "== Log de la API (errores)"; docker logs $API 2>&1 | grep -vE "^INFO|^  File|^    " | head -30
exit $rc
