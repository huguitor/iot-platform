# Alta automática de equipos (fábrica + reclamo por QR)

- **Estado:** rama `feature/alta-equipos`, probada en entorno aislado
  (2026-11-07). **No desplegada.** Es la etapa 2 del
  [plan](plan-alta-automatica-equipos.md); la etapa 1 (broker) está en §3.3 del plan.
- **Requiere:** empresas en la plataforma (revisión `20261107_01`, ya en producción).

## Cómo funciona

1. **Fábrica** (notebook, etapa 3): `POST /api/factory/devices` con el ID del
   equipo. La API crea su usuario MQTT en el broker (Dynamic Security), lo
   registra **sin empresa** y devuelve **una sola vez** el secreto MQTT y el
   código de reclamo. El secreto va al equipo por USB; el código, a la etiqueta QR.
2. El equipo se conecta solo a la plataforma con su usuario. Mientras no tenga
   empresa, **solo el superadmin** lo ve.
3. **Reclamo:** el **administrador** de una empresa manda el ID y el código del
   QR. El equipo pasa a su empresa y el código deja de servir.
4. La empresa ve el historial **solo desde ese momento** (`company_since`).

## API

| Método y ruta | Quién | Qué hace |
|---|---|---|
| `POST /api/factory/devices` | encabezado `X-Factory-Token` o superadmin | `{"device_id": "ius-000fe9e2", "name"?}`. Devuelve `mqtt_username`, `mqtt_password`, `claim_code` (`XXXX-XXXX-XXXX`) y `claim_url`. 409 si ya existe. |
| `POST /api/factory/devices?reprovision=true` | ídem | Equipo ya fabricado y **sin empresa** (p. ej. se perdió el secreto al grabarlo): secreto y código nuevos; los anteriores dejan de servir. |
| `POST /api/devices/claim` | **solo admin** de empresa | `{"device_id", "claim_code"}`. Acepta el código en minúsculas, con o sin guiones o espacios. |
| `POST /api/admin/devices/{id}/release` | superadmin | Deja el equipo sin empresa, borra los permisos de usuarios y devuelve un **código nuevo** (etiqueta nueva). Solo equipos de fábrica. |
| `PUT /api/admin/devices/{id}/mqtt` | superadmin | `{"enabled": false}` deshabilita el usuario MQTT y **corta la conexión** (robo, baja); `true` lo vuelve a habilitar. |
| `PUT /api/admin/devices/{id}` | superadmin | (ya existía) asignación manual. Ahora además marca `company_since` e invalida el código del QR. Quitar la empresa así **no** genera código: para eso, `release`. |

`GET /api/devices…` muestra además `model`, `provisioned_at`, `company_since` y
`mqtt_enabled`.

### Reglas

- ID: `ius-<chipid>` o `gw-<chipid>`, chip ID en 8 hex **minúsculas**. El modelo
  sale del prefijo (`ius-device`, `gateway-wiegand`). Cualquier otro ID se
  rechaza (422), así que no puede chocar con usuarios del passwordfile
  (`lab-esp8266-dell-01`).
- Reclamo fallido: siempre `400 "Equipo o código inválido"`, exista o no el
  equipo, ya usado o código erróneo (mismo tiempo de respuesta: se compara contra
  un hash falso).
- Límite de intentos de reclamo cada 15 min: 10 por usuario, 30 por IP, 10 por
  equipo (429). Si Redis no responde se deja pasar, como en la integración: el
  código tiene ~59 bits y se guarda con hash lento.
- Todo queda en auditoría: `device_provisioned`, `device_reprovisioned`,
  `device_claimed`, `device_claim_failed`, `device_released`,
  `device_mqtt_enabled` / `device_mqtt_disabled`. Sin secretos ni códigos.
- `company_since` se marca en **todo** cambio de empresa (reclamo, asignación
  manual, movida). Filtra la telemetría y los eventos MQTT para usuarios de la
  empresa y para el superadmin con `X-Company-Id`. Los eventos de acceso y la
  auditoría ya quedaban con la empresa del momento (triggers de `20261107_01`).
  Los equipos que ya tenían empresa quedan con `company_since` vacío: sin límite.
- Lo que **no** se filtra por fecha: el estado actual del equipo en `devices`
  (último estado de puerta, última tarjeta, versión). Se pisa con lo próximo que
  publique el equipo.

## Base de datos (migración `20261107_02`)

Columnas nullable en `devices`: `model`, `provisioned_at`, `claim_code_hash`,
`company_since`, `mqtt_enabled`. Sin datos nuevos, idempotente. La API anterior
las ignora: volver atrás no requiere tocar la base. `downgrade` deshabilitado.

## Configuración nueva de la API (`.env`)

| Variable | Valor |
|---|---|
| `MQTT_DYNSEC_HOST` / `MQTT_DYNSEC_PORT` | `mosquitto` / `1883` (por defecto) |
| `MQTT_DYNSEC_USERNAME` / `MQTT_DYNSEC_PASSWORD` | cuenta `api-admin` del passwordfile **interno** (secreto, la crea root) |
| `FACTORY_API_TOKEN` | token largo para la notebook (secreto; cambiarlo lo revoca) |
| `CLAIM_BASE_URL` | `https://iot.panozoelectronica.com.ar` (por defecto) |
| `CLAIM_RATE_LIMIT_*` | opcionales (ver arriba) |

Sin `MQTT_DYNSEC_*` la API funciona igual y las rutas de fábrica y de MQTT
responden 503. Se puede desplegar la API antes que el broker.

Dependencia nueva: `paho-mqtt==1.6.1` (la misma del worker).

## Broker (etapa 5, requiere root)

Lo que se probó en la etapa 1 y que este entorno de prueba reproduce:

1. `plugin_load dynsec …` **antes** de `plugin_load public_passwords`, y
   `plugin_use dynsec` en el listener 8883.
2. `api-admin` en el passwordfile interno y en la ACL interna con
   `topic readwrite $CONTROL/dynamic-security/#`.
3. `dynamic-security.json` en el volumen de secretos, con backup.
4. Después de `mosquitto_ctrl dynsec init`: **borrar la cuenta inicial** y
   `setDefaultACLAccess publishClientReceive deny`.
5. El rol `device` (`iot/devices/%u/#`) lo crea la API sola la primera vez.

## Pruebas

```bash
docs/run-alta-tests.sh
```

PostgreSQL, Redis, Mosquitto 2.1.2 (misma imagen que producción, con TLS de
prueba) y la API nueva, descartables y **sin red**. Construye la imagen de la API
desde la rama (necesita bajar `paho-mqtt`).

1. Esquema de producción anterior a empresas (`4542465`) con datos sintéticos.
2. `alembic upgrade head` (`20261107_01` + `20261107_02`), segunda corrida y
   verificación de los triggers de empresas.
3. API con `API_STARTUP_READ_ONLY=true` y otra sin Dynamic Security (503).
4. Pruebas de empresas de la etapa 0 (10), con el código nuevo.
5. `api/tests/alta/test_alta.py` (15): fábrica (token, formato, 409, 503),
   conexión real del equipo por TLS con su secreto y aislamiento de topics,
   reclamo (solo admin, error único, código de un solo uso, minúsculas y sin
   guiones), historial desde el reclamo, auditoría sin secretos, límite de
   intentos, liberar, deshabilitar MQTT (corta la conexión), reprovisionar,
   asignación manual, equipos viejos intactos.
6. Integración (`live_access_integration.py`) y `tests.test_startup`.
7. El log de la API no contiene secretos.

Resultado 2026-11-07: **todo OK**.
