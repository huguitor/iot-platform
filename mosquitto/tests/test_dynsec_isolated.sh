#!/bin/bash
# Etapa 1: prueba aislada de Mosquitto Dynamic Security (broker descartable, sin red)
# Uso:  bash mosquitto/tests/test_dynsec_isolated.sh
# - Contenedor descartable con la imagen de producción, --network none, TLS con
#   certificado autofirmado y contraseñas falsas. No toca el broker real.
# - Configuración = mosquitto-tls.conf de producción + Dynamic Security en 8883.
# Variables (para reproducir lo que falla):
#   LOAD_DYN_FIRST=0   carga el passwordfile público antes que dynsec (los
#                      equipos dynsec quedan rechazados: CONNACK 5)
#   ORDER=pwfile_first|dynsec_first|dynsec_only   orden de plugin_use en 8883
#   VARIANT=pattern|nopattern   acl-public con o sin línea "pattern"
#   KEEP_DYNADMIN=1 / KEEP_DEFAULT_RECEIVE=1   no borra la cuenta inicial / no
#                      cambia publishClientReceive a deny (muestra la fuga)
#   ADMIN_LISTENER=1   agrega un listener 1884 solo con dynsec
# Resultado 2026-11-07 (Mosquitto 2.1.2): 37 OK, 0 fallas.
set -u   # sin pipefail: los 'grep -q' cortan el pipe y darían falsos negativos
IMG=eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408
BASE=${TMPDIR:-/tmp}/dynsec-test
mkdir -p "$BASE"
W=$(mktemp -d "$BASE/w.XXXX")
C=dynsec-test-$$
U="$(id -u):$(id -g)"
VARIANT=${VARIANT:-pattern}
PASS=0; FAILN=0
ok()  { echo "  ✅ $*"; PASS=$((PASS+1)); }
bad() { echo "  ❌ $*"; FAILN=$((FAILN+1)); }
cleanup() { docker rm -f $C >/dev/null 2>&1; rm -rf "$W"; }
trap cleanup EXIT

mkdir -p $W/config $W/public-auth $W/tls $W/dynsec $W/data
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost -addext subjectAltName=DNS:localhost \
  -keyout $W/tls/privkey.pem -out $W/tls/fullchain.pem 2>/dev/null
# Contraseñas falsas, solo para esta prueba
docker run --rm --network none --user $U -v $W:/w --entrypoint sh $IMG -c '
  mosquitto_passwd -c -b /w/config/passwordfile worker wpass &&
  mosquitto_passwd -b /w/config/passwordfile api-admin adminpass &&
  mosquitto_passwd -c -b /w/public-auth/passwordfile lab-01 labpass &&
  mosquitto_ctrl dynsec init /w/dynsec/dynamic-security.json dynadmin dynadminpass' >/dev/null 2>&1 || { echo "setup falló"; exit 1; }
cat > $W/config/acl <<'EOF'
user worker
topic readwrite iot/devices/#
user api-admin
topic readwrite $CONTROL/dynamic-security/#
EOF
{ echo "user lab-01"; echo "topic readwrite iot/devices/lab-01/#";
  [ "$VARIANT" = pattern ] && echo "pattern readwrite iot/devices/%u/#"; } > $W/config/acl-public
ORDER=${ORDER:-pwfile_first}
case $ORDER in
  pwfile_first) PUBLIC_PLUGINS=$'plugin_use public_passwords\nplugin_use public_acl\nplugin_use dynsec';;
  dynsec_first) PUBLIC_PLUGINS=$'plugin_use dynsec\nplugin_use public_passwords\nplugin_use public_acl';;
  dynsec_only)  PUBLIC_PLUGINS='plugin_use dynsec';;
esac
PUBLIC_LOAD="plugin_load public_passwords /usr/lib/mosquitto_password_file.so
plugin_opt_password_file /mosquitto/public-auth/passwordfile
plugin_load public_acl /usr/lib/mosquitto_acl_file.so
plugin_opt_acl_file /mosquitto/config/acl-public"
DYN_LOAD="plugin_load dynsec /usr/lib/mosquitto_dynamic_security.so
plugin_opt_config_file /mosquitto/dynsec/dynamic-security.json"
[ $ORDER = dynsec_only ] && PUBLIC_LOAD=""
if [ "${LOAD_DYN_FIRST:-1}" = 1 ]; then LOADS="$DYN_LOAD
$PUBLIC_LOAD"; else LOADS="$PUBLIC_LOAD
$DYN_LOAD"; fi
ADMIN_BLOCK=""
[ -n "${ADMIN_LISTENER:-}" ] && ADMIN_BLOCK="listener 1884
listener_allow_anonymous false
plugin_use dynsec"
cat > $W/config/mosquitto.conf <<EOF
persistence true
persistence_location /mosquitto/data/
log_dest stdout
log_timestamp false
connection_messages true
plugin_load internal_passwords /usr/lib/mosquitto_password_file.so
plugin_opt_password_file /mosquitto/config/passwordfile
plugin_load internal_acl /usr/lib/mosquitto_acl_file.so
plugin_opt_acl_file /mosquitto/config/acl
$LOADS

listener 1883
listener_allow_anonymous false
plugin_use internal_passwords
plugin_use internal_acl

$ADMIN_BLOCK

listener 8883
listener_allow_anonymous false
use_username_as_clientid true
$PUBLIC_PLUGINS
certfile /mosquitto/tls/fullchain.pem
keyfile /mosquitto/tls/privkey.pem
tls_version tlsv1.2
require_certificate false
EOF

start() {
  docker run -d --name $C --network none --user $U \
    -v $W/config:/mosquitto/config:ro -v $W/public-auth:/mosquitto/public-auth:ro \
    -v $W/tls:/mosquitto/tls:ro -v $W/dynsec:/mosquitto/dynsec -v $W/data:/mosquitto/data \
    $IMG mosquitto -c /mosquitto/config/mosquitto.conf >/dev/null
  for i in $(seq 20); do docker logs $C 2>&1 | grep -q 'listen socket on port 8883' && break; sleep 0.3; done
  sleep 0.5
  docker ps -q -f name=$C | grep -q . || { docker logs $C 2>&1 | grep -vE "^(Warning|To fix)"; echo "broker no arrancó"; exit 1; }
}
X() { docker exec $C "$@"; }
TLS="-p 8883 --cafile /mosquitto/tls/fullchain.pem -h localhost"
delivered_to_worker() { # user pass topic: lo que publica en 8883, ¿le llega al worker?
  X sh -c "mosquitto_sub -p 1883 -u worker -P wpass -t '$3' -C 1 -W 3 > /tmp/out 2>&1 &
           sleep 0.7; mosquitto_pub $TLS -u '$1' -P '$2' -t '$3' -m m$$ -q 1 >/dev/null 2>&1; wait; cat /tmp/out" | grep -q "m$$"
}
received_from_worker() { # user pass topic-sub [topic-pub]: lo que publica el worker, ¿le llega al de 8883?
  X sh -c "mosquitto_sub $TLS -u '$1' -P '$2' -t '$3' -C 1 -W 3 > /tmp/out2 2>&1 &
           sleep 1.2; mosquitto_pub -p 1883 -u worker -P wpass -t '${4:-$3}' -m r$$ -q 1; wait; cat /tmp/out2" | grep -q "r$$"
}
can_connect() { # user pass port-args: CONNACK 0 = entró
  X mosquitto_pub $3 -u "$1" -P "$2" -t 'x/never' -m x -d 2>&1 | grep -q 'received CONNACK (0)'
}
ctrl() { X mosquitto_ctrl "$@" 2>&1; }

echo "== orden 8883: $ORDER | carga dynsec primero: ${LOAD_DYN_FIRST:-1} | acl-public: $VARIANT | listener admin 1884: ${ADMIN_LISTENER:+sí}${ADMIN_LISTENER:-no}"
start

echo "-- Cuenta de administración"
ADMIN=""
ADMIN_B="-p 1883 -u api-admin -P adminpass"
OUT=$(ctrl $ADMIN_B dynsec listClients)
if echo "$OUT" | grep -q dynadmin; then ok "api-admin (passwordfile interno) administra dynsec por 1883, sin dynsec en ese listener"; ADMIN="$ADMIN_B"
else echo "  ℹ️  api-admin interno no administra por 1883: $(echo "$OUT" | head -1)"; fi
if [ -n "${ADMIN_LISTENER:-}" ]; then
  ctrl -p 1884 -u dynadmin -P dynadminpass dynsec listClients | grep -q dynadmin && ok "dynadmin administra por 1884" || bad "dynadmin no administra por 1884"
  [ -z "$ADMIN" ] && ADMIN="-p 1884 -u dynadmin -P dynadminpass"
fi
OUT=$(ctrl $TLS -u dynadmin -P dynadminpass dynsec listClients)
echo "$OUT" | grep -q dynadmin && echo "  ℹ️  la cuenta inicial dynadmin administra desde 8883 mientras exista (hay que borrarla)" || ok "dynadmin no administra desde 8883"
[ -n "$ADMIN" ] || { echo "sin cuenta admin, no sigo"; exit 1; }
if [ -z "${KEEP_DYNADMIN:-}" ]; then
  ctrl $ADMIN dynsec deleteClient dynadmin >/dev/null
  OUT=$(ctrl $TLS -u dynadmin -P dynadminpass dynsec listClients)
  echo "$OUT" | grep -q dynadmin && bad "dynadmin borrado sigue administrando por 8883" || ok "dynadmin borrado: no administra por 8883 ($(echo "$OUT" | head -1))"
  ctrl $ADMIN dynsec listClients >/dev/null && ok "api-admin sigue administrando sin dynadmin" || bad "api-admin no administra sin dynadmin"
fi

if [ -z "${KEEP_DEFAULT_RECEIVE:-}" ]; then
  ctrl $ADMIN dynsec setDefaultACLAccess publishClientReceive deny >/dev/null

fi

echo "-- Alta de equipos (un solo rol con %u)"
ctrl $ADMIN dynsec createRole device >/dev/null
for t in publishClientSend publishClientReceive subscribePattern unsubscribePattern; do
  ctrl $ADMIN dynsec addRoleACL device $t 'iot/devices/%u/#' allow 5 >/dev/null
done
D=ius-000FE9E2; DP=devpass1; D2=gw-00AABBCC
for c in "$D $DP" "$D2 devpass2"; do set -- $c; ctrl $ADMIN dynsec createClient $1 -p $2 >/dev/null; ctrl $ADMIN dynsec addClientRole $1 device >/dev/null; done
ctrl $ADMIN dynsec getClient $D | grep -q device && ok "creados $D y $D2 sin reiniciar el broker" || bad "no se crearon"

echo "-- Convivencia en 8883"
delivered_to_worker lab-01 labpass iot/devices/lab-01/state && ok "lab-01 (passwordfile) publica en su topic" || bad "lab-01 no publica en su topic"
received_from_worker lab-01 labpass iot/devices/lab-01/cmd && ok "lab-01 recibe su cmd" || bad "lab-01 no recibe su cmd"
delivered_to_worker lab-01 labpass iot/devices/$D/state && bad "lab-01 publica en topic de $D" || ok "lab-01 no publica en topic ajeno"
delivered_to_worker $D $DP iot/devices/$D/state && ok "$D (dynsec) publica en su topic (%u funciona)" || bad "$D no publica en su topic"
received_from_worker $D $DP iot/devices/$D/cmd && ok "$D recibe su cmd" || bad "$D no recibe su cmd"
delivered_to_worker $D2 devpass2 iot/devices/$D2/state && ok "$D2 publica con el mismo rol" || bad "$D2 no publica"
delivered_to_worker $D $DP iot/devices/$D2/state && bad "$D publica en topic de $D2" || ok "$D no publica en topic de otro equipo"
delivered_to_worker $D $DP iot/devices/lab-01/state && bad "$D publica en topic de lab-01" || ok "$D no publica en topic de lab-01"
received_from_worker $D $DP iot/devices/$D2/cmd && bad "$D recibe cmd de $D2" || ok "$D no recibe cmd ajeno"
received_from_worker lab-01 labpass iot/devices/$D/cmd && bad "lab-01 recibe cmd de $D" || ok "lab-01 no recibe cmd de $D"
received_from_worker lab-01 labpass 'iot/devices/#' iot/devices/$D2/cmd && bad "lab-01 lee todo iot/devices/#" || ok "lab-01 no puede leer iot/devices/#"
ctrl $TLS -u $D -P $DP dynsec createClient intruso -p x >/dev/null; ctrl $ADMIN dynsec getClient intruso | grep -q intruso && bad "$D creó un usuario por \$CONTROL" || ok "$D no puede administrar (\$CONTROL)"
ctrl $TLS -u lab-01 -P labpass dynsec createClient intruso2 -p x >/dev/null; ctrl $ADMIN dynsec getClient intruso2 | grep -q intruso2 && bad "lab-01 creó un usuario por \$CONTROL" || ok "lab-01 no puede administrar (\$CONTROL)"
received_from_worker $D $DP 'iot/devices/#' iot/devices/$D2/cmd && bad "$D lee todo iot/devices/#" || ok "$D no puede leer iot/devices/#"

echo "-- Autenticación"
can_connect $D malpass "$TLS" && bad "$D entra con clave incorrecta" || ok "$D con clave incorrecta: rechazado"
can_connect lab-01 malpass "$TLS" && bad "lab-01 entra con clave incorrecta" || ok "lab-01 con clave incorrecta: rechazado"
can_connect nadie x "$TLS" && bad "usuario inexistente entra" || ok "usuario inexistente: rechazado"
can_connect $D $DP "-p 1883" && bad "$D entra por 1883 interno" || ok "$D no entra por 1883 interno"
can_connect worker wpass "$TLS" && bad "worker entra por 8883" || ok "worker (interno) no entra por 8883"
can_connect api-admin adminpass "$TLS" && bad "api-admin entra por 8883" || ok "api-admin no entra por 8883"
docker logs $C 2>&1 | grep -q "as $D " && ok "client id = usuario ($D)" || bad "client id distinto del usuario"

echo "-- Deshabilitar, cambiar clave, borrar"
X sh -c "mosquitto_sub $TLS -u $D -P $DP -t iot/devices/$D/cmd -W 8 >/dev/null 2>&1; echo \$? > /tmp/rc" &
BG=$!
sleep 1.5; ctrl $ADMIN dynsec disableClient $D >/dev/null; wait $BG
RC=$(X cat /tmp/rc)
[ "$RC" != 27 ] && ok "disableClient corta la conexión activa (rc=$RC)" || bad "disableClient no cortó la conexión activa"
can_connect $D $DP "$TLS" && bad "$D deshabilitado entra" || ok "$D deshabilitado no entra"
ctrl $ADMIN dynsec enableClient $D >/dev/null
can_connect $D $DP "$TLS" && ok "enableClient: vuelve a entrar" || bad "enableClient: no entra"
ctrl $ADMIN dynsec setClientPassword $D nuevaclave >/dev/null
can_connect $D $DP "$TLS" && bad "la clave vieja sigue sirviendo" || ok "cambio de clave: la vieja no sirve"
can_connect $D nuevaclave "$TLS" && ok "cambio de clave: la nueva sirve" || bad "la nueva clave no sirve"
DP=nuevaclave
ctrl $ADMIN dynsec deleteClient $D2 >/dev/null
can_connect $D2 devpass2 "$TLS" && bad "$D2 borrado entra" || ok "deleteClient: $D2 no entra"

echo "-- Mismo usuario en passwordfile y en dynsec"
ctrl $ADMIN dynsec createClient lab-01 -p otraclave >/dev/null; ctrl $ADMIN dynsec addClientRole lab-01 device >/dev/null
can_connect lab-01 labpass "$TLS" && echo "  ℹ️  lab-01 entra con la clave del passwordfile" || echo "  ℹ️  lab-01 NO entra con la clave del passwordfile"
can_connect lab-01 otraclave "$TLS" && echo "  ℹ️  lab-01 entra con la clave de dynsec" || echo "  ℹ️  lab-01 NO entra con la clave de dynsec"
ctrl $ADMIN dynsec deleteClient lab-01 >/dev/null

echo "-- Persistencia (reinicio del broker)"
docker restart $C >/dev/null; sleep 1.5
can_connect $D $DP "$TLS" && ok "$D sigue después de reiniciar" || bad "$D se perdió al reiniciar"
delivered_to_worker $D $DP iot/devices/$D/state && ok "$D publica después de reiniciar" || bad "$D no publica después de reiniciar"
grep -q "\"$D\"" $W/dynsec/dynamic-security.json && ok "guardado en dynamic-security.json" || bad "no está en el json"
ctrl $ADMIN dynsec getDefaultACLAccess | grep -q 'publishClientReceive : deny' && ok "default publishClientReceive=deny persiste" || bad "default receive volvió a allow"
ctrl $ADMIN dynsec getClient dynadmin | grep -q 'Username: *dynadmin' && bad "dynadmin reapareció" || ok "dynadmin sigue borrado"
grep -q nuevaclave $W/dynsec/dynamic-security.json && bad "clave en texto plano en el json" || ok "el json no guarda la clave en texto plano"

echo "-- Avisos del broker"
docker logs $C 2>&1 | grep -iE "error|warn" | sort | uniq -c | head -8
echo
echo "== Resultado ($ORDER, $VARIANT${ADMIN_LISTENER:+, admin 1884}): $PASS OK, $FAILN fallas"
[ $FAILN -eq 0 ]
