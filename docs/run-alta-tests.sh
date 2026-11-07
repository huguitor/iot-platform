#!/bin/bash
# Prueba aislada del alta automática de equipos (etapa 2 del plan).
# PostgreSQL, Redis, Mosquitto con Dynamic Security y la API nueva, todos
# descartables y SIN red (comparten el namespace de red del contenedor de la
# base). Contraseñas y certificado de prueba. No toca producción.
#
# Uso: docs/run-alta-tests.sh
# Ver docs/alta-automatica-equipos.md.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
NEW=$REPO/api
H=$REPO/api/tests/alta
HE=$REPO/api/tests/empresas
BASE_REF=4542465   # producción antes de empresas (revisión 20261104_01)
MOSQ_IMG=eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408
IMG=iot-platform-api:alta-test
SFX=$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')
PG=iot-alta-test-pg-$SFX; RD=iot-alta-test-redis-$SFX; MQ=iot-alta-test-mqtt-$SFX
API=iot-alta-test-api-$SFX; API2=iot-alta-test-api2-$SFX
DSN=postgresql://postgres@127.0.0.1:5432/iot_alta_test
W=$(mktemp -d); BASE=$(mktemp -d)
U="$(id -u):$(id -g)"
ADMIN_PW=admin-$SFX; WORKER_PW=worker-$SFX; FACTORY=fabrica-$SFX; LAB_PW=lab-$SFX
cleanup(){ docker rm -f $API $API2 $MQ $RD $PG >/dev/null 2>&1; rm -rf "$W" "$BASE"; }
trap cleanup EXIT
rc=0

echo "== 0. Imagen de la API con las dependencias nuevas (paho-mqtt)"
docker build -q -t $IMG "$NEW" >/dev/null || { echo "build falló"; exit 1; }
git -C "$REPO" archive "$BASE_REF" api | tar -x -C "$BASE"

COMMON=(--network container:$PG --read-only --tmpfs /tmp --memory 512m --pids-limit 256
  -e EMPRESAS_TEST_DISPOSABLE=1 -e ALTA_TEST_DISPOSABLE=1 -e DATABASE_URL=$DSN -e JWT_SECRET=solo-prueba-$SFX
  -e API_ADMIN_USER=sa-test -e API_ADMIN_PASSWORD=Prueba-SA-1234 -e REDIS_URL=redis://127.0.0.1:6399/0
  -e ACCESS_INTEGRATION_API_KEY=clave-prueba-$SFX -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app)
DYN=(-e MQTT_DYNSEC_HOST=127.0.0.1 -e MQTT_DYNSEC_PORT=1883 -e MQTT_DYNSEC_USERNAME=api-admin
  -e MQTT_DYNSEC_PASSWORD=$ADMIN_PW -e FACTORY_API_TOKEN=$FACTORY -e CLAIM_BASE_URL=https://iot.ejemplo.test)

echo "== 1. Base, Redis y broker descartables"
docker run -d --name $PG --network none --memory 512m --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=iot_alta_test postgres:15 >/dev/null
for i in $(seq 30); do docker exec $PG pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker run -d --name $RD --network container:$PG --memory 64m redis:7-alpine \
  redis-server --port 6399 --save '' --appendonly no >/dev/null

mkdir -p $W/config $W/public-auth $W/tls $W/dynsec $W/data
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost -addext subjectAltName=DNS:localhost \
  -keyout $W/tls/privkey.pem -out $W/tls/fullchain.pem 2>/dev/null
docker run --rm --network none --user $U -v $W:/w --entrypoint sh $MOSQ_IMG -c "
  mosquitto_passwd -c -b /w/config/passwordfile worker-test $WORKER_PW &&
  mosquitto_passwd -b /w/config/passwordfile api-admin $ADMIN_PW &&
  mosquitto_passwd -c -b /w/public-auth/passwordfile lab-test $LAB_PW &&
  mosquitto_ctrl dynsec init /w/dynsec/dynamic-security.json dynsec-init init-$SFX" >/dev/null 2>&1 || { echo "setup del broker falló"; exit 1; }
printf 'user worker-test\ntopic readwrite iot/devices/#\nuser api-admin\ntopic readwrite $CONTROL/dynamic-security/#\n' > $W/config/acl
printf 'user lab-test\ntopic readwrite iot/devices/lab-test/#\n' > $W/config/acl-public
cat > $W/config/mosquitto.conf <<'EOF'
persistence false
log_dest stdout
connection_messages true
# dynsec se carga ANTES que el passwordfile público (etapa 1)
plugin_load dynsec /usr/lib/mosquitto_dynamic_security.so
plugin_opt_config_file /mosquitto/dynsec/dynamic-security.json
plugin_load internal_passwords /usr/lib/mosquitto_password_file.so
plugin_opt_password_file /mosquitto/config/passwordfile
plugin_load internal_acl /usr/lib/mosquitto_acl_file.so
plugin_opt_acl_file /mosquitto/config/acl
plugin_load public_passwords /usr/lib/mosquitto_password_file.so
plugin_opt_password_file /mosquitto/public-auth/passwordfile
plugin_load public_acl /usr/lib/mosquitto_acl_file.so
plugin_opt_acl_file /mosquitto/config/acl-public

listener 1883
listener_allow_anonymous false
plugin_use internal_passwords
plugin_use internal_acl

listener 8883
listener_allow_anonymous false
use_username_as_clientid true
plugin_use public_passwords
plugin_use public_acl
plugin_use dynsec
certfile /mosquitto/tls/fullchain.pem
keyfile /mosquitto/tls/privkey.pem
tls_version tlsv1.2
EOF
docker run -d --name $MQ --network container:$PG --user $U --memory 64m \
  -v $W/config:/mosquitto/config:ro -v $W/public-auth:/mosquitto/public-auth:ro \
  -v $W/tls:/mosquitto/tls:ro -v $W/dynsec:/mosquitto/dynsec $MOSQ_IMG mosquitto -c /mosquitto/config/mosquitto.conf >/dev/null
for i in $(seq 20); do docker logs $MQ 2>&1 | grep -q 'port 8883' && break; sleep 0.3; done
# Preparación del broker (lo mismo que hará la etapa 5): sin cuenta inicial y sin recibir por defecto.
CTRL="mosquitto_ctrl -h 127.0.0.1 -p 1883 -u api-admin -P $ADMIN_PW dynsec"
docker exec $MQ sh -c "$CTRL deleteClient dynsec-init && $CTRL setDefaultACLAccess publishClientReceive deny" >/dev/null 2>&1 \
  && echo "  broker listo (cuenta inicial borrada, publishClientReceive=deny)" || { echo "  ❌ preparación del broker"; rc=1; }

echo "== 2. Esquema de producción (antes de empresas) + datos sintéticos"
docker run --rm "${COMMON[@]}" -v "$BASE/api":/app:ro -v "$HE":/h:ro -w /app --entrypoint python $IMG /h/baseline.py || rc=1
docker run --rm "${COMMON[@]}" -v "$BASE/api":/app:ro -w /app --entrypoint python $IMG -m alembic stamp 20261104_01 2>&1 | tail -1
echo "== 3. Migraciones nuevas (20261107_01 empresas + 20261107_02 alta)"
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic heads 2>&1 | tail -1
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic upgrade head 2>&1 | tail -2
docker exec $PG psql -U postgres -d iot_alta_test -Atc "select version_num from alembic_version" | sed 's/^/  revisión: /'
docker exec $PG psql -U postgres -d iot_alta_test -qc "update alembic_version set version_num='20261107_01'"
docker run --rm "${COMMON[@]}" -v "$NEW":/app:ro -w /app --entrypoint python $IMG -m alembic upgrade head >/dev/null 2>&1 \
  && echo "  20261107_02 idempotente" || { echo "  ❌ segunda corrida de 20261107_02"; rc=1; }
docker run --rm "${COMMON[@]}" -e EXPECTED_ALEMBIC=20261107_02 -v "$HE":/h:ro --entrypoint python $IMG /h/verify_schema.py >/dev/null || { echo "  ❌ verify_schema"; rc=1; }   # triggers de empresas
docker exec $PG psql -U postgres -d iot_alta_test -Atc "select string_agg(column_name, ',' order by column_name) from information_schema.columns where table_name='devices' and column_name in ('model','provisioned_at','claim_code_hash','company_since','mqtt_enabled')" | sed 's/^/  columnas nuevas: /'

echo "== 4. API nueva (API_STARTUP_READ_ONLY=true) y una sin Dynamic Security"
docker run -d --name $API "${COMMON[@]}" "${DYN[@]}" -e API_STARTUP_READ_ONLY=true -v "$NEW":/app:ro -w /app --entrypoint uvicorn $IMG app.main:app --host 127.0.0.1 --port 18999 >/dev/null
docker run -d --name $API2 "${COMMON[@]}" -e FACTORY_API_TOKEN=$FACTORY -e API_STARTUP_READ_ONLY=true -v "$NEW":/app:ro -w /app --entrypoint uvicorn $IMG app.main:app --host 127.0.0.1 --port 18998 >/dev/null
for p in 18999 18998; do
  for i in $(seq 30); do docker run --rm --network container:$PG $IMG python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$p/api/health',timeout=2)" >/dev/null 2>&1 && break; sleep 1; done
done

echo "== 5. Pruebas de empresas (etapa 0) con el código nuevo (antes: esperan los equipos del baseline)"
docker run --rm "${COMMON[@]}" -e IOT_TEST_URL=http://127.0.0.1:18999 -e IOT_TEST_KEY=clave-prueba-$SFX -v "$HE":/h:ro --entrypoint python $IMG /h/test_empresas.py 2>&1 | grep -E "^(Ran|OK|FAIL|ERROR)" || rc=1
echo "== 6. Pruebas del alta automática"
docker run --rm "${COMMON[@]}" -e IOT_TEST_URL=http://127.0.0.1:18999 -e IOT_TEST_URL_NODYN=http://127.0.0.1:18998 \
  -e IOT_TEST_DSN=$DSN -e IOT_TEST_FACTORY=$FACTORY -e IOT_TEST_WORKER_PW=$WORKER_PW -e IOT_TEST_LAB_PW=$LAB_PW \
  -e IOT_TEST_CA=/tls/fullchain.pem -v $W/tls/fullchain.pem:/tls/fullchain.pem:ro -v "$H":/h:ro \
  --entrypoint python $IMG /h/test_alta.py 2>&1 | grep -E "^(test_|FAIL|ERROR|AssertionError|Ran|OK|  File \"/h|[A-Za-z]+Error)" || rc=1
echo "== 7. Pruebas existentes (integración y arranque)"
docker run --rm "${COMMON[@]}" -e IOT_TEST_URL=http://127.0.0.1:18999 -e IOT_TEST_KEY=clave-prueba-$SFX -e IOT_TEST_DSN=$DSN -v "$NEW":/app:ro -w /app/tests --entrypoint python $IMG live_access_integration.py 2>&1 | tail -2
docker run --rm --network none --read-only --tmpfs /tmp -v "$NEW":/app:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python $IMG -m unittest tests.test_startup 2>&1 | tail -1
echo "== 8. Secretos fuera de los logs"
if docker logs $API 2>&1 | grep -qiE "mqtt_password|$ADMIN_PW"; then echo "  ❌ el log de la API tiene secretos"; rc=1; else echo "  log de la API sin secretos"; fi
echo "== Log de la API (errores)"; docker logs $API 2>&1 | grep -vE "^INFO|^  File|^    " | head -20
echo "== Log del broker (dynsec)"; docker logs $MQ 2>&1 | grep -E "dynsec:" | sed -E 's/password=[^ ]+/password=***/' | awk '{print $3, $4, $5}' | sort | uniq -c | head -12
exit $rc
