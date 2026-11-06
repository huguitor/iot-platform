# Integración con Control de Acceso

Estado: 2026-11-05. Rama `feature/integracion-control-acceso`.

La app **Control de Acceso** (`/srv/control-acceso`, https://acceso.panozoelectronica.com.ar)
es la dueña de las empresas, las personas, las tarjetas y las marcaciones. IoT Platform sigue
siendo la capa de dispositivos: MQTT, comandos, licencias y telemetría. Para eso expone una API
de integración. No hace falta cambiar el worker ni el esquema de la base.

```
Arduino UNO ⇄ ESP8266 ⇄ MQTT ⇄ mqtt-worker ⇄ PostgreSQL (mqtt_events, commands)
                                                   ⇅  /api/integrations/access/*
                                         Control de Acceso (servicio monitor, hilo "iot")
```

## Autenticación

- Encabezado `X-API-Key` con `ACCESS_INTEGRATION_API_KEY` del `.env`.
- Es una clave distinta de `INTEGRATION_API_KEY`, la del chat.
- Si la variable está vacía, la API responde 503.
- En Control de Acceso la misma clave va en `backend/.env` como `IOT_API_KEY`, junto con `IOT_API_URL`.
- Nunca copiar el valor a documentación ni a Git.

## Endpoints (`/api/integrations/access`)

| Método y ruta | Uso |
|---|---|
| `GET /devices?device_ids=a,b` | Estado, `last_seen`, firmware y capacidad. |
| `GET /events/cursor` | Último id de `mqtt_events` entrante. Control de Acceso arranca desde ahí y no importa el historial previo. |
| `GET /events?after_id=N&device_ids=a,b&limit=500` | Eventos publicados en `iot/devices/<id>/event`, ordenados por id. Ver detalle abajo. |
| `POST /devices/{id}/commands` | Encola un comando en `commands` y el worker lo publica en `/cmd`. Ver detalle abajo. |
| `GET /commands/{id}` | Estado del comando: `pending`, `dispatched` o `failed`. |

Respuesta de `GET /events`:

- `occurred_at` descuenta `delay_s`;
- no se devuelven los duplicados que detecta el worker;
- incluye `next_after_id` y `more`.

Reglas de `POST /devices/{id}/commands`:

- Comandos permitidos: `enroll` y `delete` (con `card_uid`), `list` (opcionalmente `start`/`count` ≤ 50), `sum`, `version` y `stats`.
- `reset`, salidas y licencias no se aceptan.
- `card_uid` debe tener solo dígitos, entre 1 y 16777214; se aceptan ceros a la izquierda.
- Cada comando queda en `audit_logs` como `integration_command_requested`.

## Lo que hace Control de Acceso

- **Alta del equipo:**
  - el superadmin crea un panel con conexión «Controlador IoT»;
  - el ID del dispositivo de esta plataforma va en el campo «número de serie»;
  - el equipo queda asignado a una empresa;
  - un administrador de empresa no puede vincular ni cambiar el ID.
- **Marcaciones:**
  - `access` se registra como apertura normal (0), acceso denegado (23) o tarjeta no registrada (27);
  - `button`/`door_request` se registra como 202, `door`/`door_state` como 200/201 (además actualiza el sensor) y `boot` como 206 (además actualiza el firmware);
  - lectora `L1` = puerta 1, `L2` = puerta 2;
  - el cursor y los eventos se guardan en la misma transacción.
- **Tarjetas:**
  - el sistema da de alta las tarjetas de las personas activas, vigentes, con nivel de acceso en las puertas del equipo, y da de baja las que dejaron de estarlo;
  - una tarjeta cuenta como cargada recién cuando el equipo responde `ENROLL ... OK/EXISTS`;
  - sin respuesta en 5 minutos el comando queda en error y se vuelve a intentar;
  - también se replanifica cada 10 minutos;
  - solo se borran tarjetas que envió el sistema: las grabadas con la tarjeta maestra no se tocan.
- **Sincronizar** (botón): replanifica y pide `list`. El listado queda en `adms_state.listed`.

Límites:

- el controlador decide solo: no aplica horarios ni feriados;
- no hay apertura remota.

## Pruebas

- `api/tests/live_access_integration.py`: 4 pruebas contra una API real con PostgreSQL descartable. Script de ejemplo en el traspaso del 2026-11-05. No ejecutarlas contra producción.
- Control de Acceso: `dispositivos/tests_iot.py`, 8 pruebas con cliente falso.

## Pendiente

- Prueba en vivo con `lab-esp8266-dell-20261104-01`: alta y baja de una tarjeta, y marcación.
- `list` paginado y `sum` dependen del gateway, que hoy es 1.6.2. El firmware UNO 2.4.0 todavía no está cargado ni versionado.
- Restringir la clave a los dispositivos de las empresas, cuando IoT Platform tenga empresas.
- Login único.
