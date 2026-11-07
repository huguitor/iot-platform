# Plan: alta automática de equipos y reclamo por QR

- **Estado:** etapas 0 (en producción), 1 y 2 (probadas en aislado, rama `feature/alta-equipos`) hechas. Etapas 3 a 6 pendientes. El alta automática **no** está en producción.
- **Fecha:** 2026-11-06, por Claude (sesión en el VPS), a pedido de Hugo.
- **Alcance:** broker MQTT público (8883), API y dashboard de iot-platform,
  firmware de los equipos (primero `esp12_serial_ius`), herramienta de "fábrica".

## 1. Decisiones de Hugo (2026-11-06)

1. Cada equipo sale de fábrica **con su usuario MQTT ya registrado** y **siempre
   se conecta** a la plataforma apenas tiene WiFi. Nadie crea usuarios a mano.
2. Un equipo nuevo queda **sin empresa**. Hay dos formas de asignarlo:
   - un **administrador de la plataforma** lo asigna a mano a una empresa;
   - la **empresa escanea el QR** del equipo y queda asignado a ella sola.
3. El QR identifica al equipo como **único** (como cámaras y alarmas).

Reglas propuestas por Claude y aceptadas en la conversación:

- El código del QR sirve **una sola vez**. Para pasar un equipo reclamado a otra
  empresa, un administrador lo libera o lo mueve.
- El QR **no contiene la contraseña MQTT**: copiar la etiqueta no permite
  hacerse pasar por el equipo.
- Lo que publica un equipo sin empresa se guarda, pero **no lo ve ninguna
  empresa**. Al reclamarlo, la empresa ve solo desde ese momento (§9).

## 2. Estado actual verificado (2026-11-06)

| Tema | Estado | Cómo se verificó |
|---|---|---|
| Broker | `eclipse-mosquitto` **2.1.2**, contenedor `iot-platform-mosquitto` | `mosquitto -h` en el contenedor |
| Plugins disponibles | `mosquitto_dynamic_security.so`, `mosquitto_password_file.so`, `mosquitto_acl_file.so` | `ls /usr/lib` en el contenedor |
| Listener 8883 (equipos) | TLS 1.2, `use_username_as_clientid true`, usuarios en `/mosquitto/public-auth/passwordfile` (secretos de root) y ACL `acl-public` | `mosquitto/config/mosquitto-tls.conf` |
| Listeners 1883/9001 (internos) | `passwordfile` + `acl` internos (worker, API) | mismo archivo |
| Alta de usuarios hoy | Manual, como root; `provision-public-auth.py` solo sirve para el equipo de laboratorio | `mosquitto/tools/provision-public-auth.py` |
| Equipos en el broker público | `lab-esp8266-dell-01` (gateway del laboratorio) | herramienta y docs de TLS |
| Empresa del equipo | `devices.company_id` y `device_in_scope()` existen **solo en la rama `wip/multiempresa-acceso` sin commit**; `master` (`4542465`) no los tiene | `git show master:api/app/models.py`, `api/app/tenancy.py` |
| Topics que usa la plataforma | `iot/devices/<id>/{cmd,state,status,info,event,telemetry,response}` | `mqtt-worker/worker.py` |

## 3. Diseño

### 3.1 Identidad de cada equipo

| Dato | Ejemplo | Dónde vive | Para qué |
|---|---|---|---|
| ID del equipo | `ius-000fe9e2` / `gw-…` (modelo + chip ID, §9) | equipo, plataforma, QR | identificarlo; es también el usuario MQTT |
| Secreto MQTT | 32 bytes aleatorios | **solo** en la memoria del equipo (LittleFS) y en el broker (con hash) | conectarse al broker |
| Código de reclamo | `K7QF-2M9X-PA4D` | **solo** en el QR/etiqueta; en la base, con hash | probar que quien reclama tiene el equipo |

- El ID se arma con el chip ID del ESP: no se repite y no hay que llevar una
  lista a mano. Para otros modelos cambia el prefijo (`gw-…` para gateways).
- El secreto **no** va en el firmware ni en git: el mismo binario sirve para
  todos los equipos.

### 3.2 Contenido del QR

Una URL, para que la cámara del celular la abra sin app propia:

```
https://iot.panozoelectronica.com.ar/agregar?d=ius-000fe9e2&c=K7QF2M9XPA4D
```

La etiqueta muestra también el ID y el código en texto, para cargarlos a mano
si el QR se daña.

### 3.3 Broker: usuarios desde la plataforma (Dynamic Security)

Recomendación: usar el plugin oficial **Dynamic Security** de Mosquitto en el
listener 8883.

- La API crea, deshabilita o borra el usuario de cada equipo mandando comandos
  al topic de control del plugin (`$CONTROL/dynamic-security/v1`), con una
  cuenta de administración propia. No hace falta root ni reiniciar el broker.
- Cada equipo tiene su usuario y un rol con permiso solo sobre
  `iot/devices/<su-ID>/#`.
- Ya viene en la imagen actual: no hay que construir una imagen propia.
- El archivo de usuarios del plugin (`dynamic-security.json`) queda en un
  volumen de secretos con backup.

Alternativa descartada por ahora: `mosquitto-go-auth` con usuarios en
PostgreSQL. Es más directa (una fila por equipo) pero obliga a construir y
mantener una imagen de Mosquitto propia.

Descartada: que la API escriba el `passwordfile` y recargue el broker. Le daría
a la API acceso de escritura a secretos de root y control sobre el contenedor.

**A verificar en la prueba aislada (etapa 1):**
- que en 2.1.2 Dynamic Security conviva con el `passwordfile` público en el
  mismo listener (para no cortar al equipo de laboratorio durante la migración);
- dónde conectar la cuenta de administración: lo más seguro es un listener
  interno aparte, sin puerto publicado, solo para la API;
- si los roles aceptan `%u` en los topics (un solo rol para todos) o hace falta
  un rol por equipo (igual es automático).

**Resultado de la etapa 1 (2026-11-07, Mosquitto 2.1.2, prueba aislada
`mosquitto/tests/test_dynsec_isolated.sh`: 37 OK, 0 fallas):**

| Punto | Resultado |
|---|---|
| Convivencia con el `passwordfile` público en 8883 | **Sí**, con una condición: `plugin_load dynsec` tiene que ir **antes** que `plugin_load public_passwords` en el archivo. El orden de `plugin_use` no importa. Con el passwordfile cargado primero, los equipos de dynsec quedan rechazados (CONNACK 5) y los del passwordfile siguen bien. |
| Cuenta de administración | Usuario `api-admin` en el **passwordfile interno** con `topic readwrite $CONTROL/dynamic-security/#` en la ACL interna, conectado por **1883** (red Docker interna). Funciona **sin** `plugin_use dynsec` en 1883 y sin listener extra. `api-admin` no entra por 8883. |
| Rol con `%u` | **Sí**: un solo rol `device` con `iot/devices/%u/#` (publishClientSend, publishClientReceive, subscribePattern, unsubscribePattern). Cada equipo publica y recibe solo lo suyo. |

Hallazgos que el despliegue (etapa 5) tiene que respetar:

1. **Borrar la cuenta inicial de dynsec** (la que crea `mosquitto_ctrl dynsec
   init`): mientras exista, administra el broker **desde el 8883 público**.
   Borrada, `api-admin` sigue administrando.
2. **`setDefaultACLAccess publishClientReceive deny`**. Por defecto dynsec deja
   recibir todo y el plugin de ACL de archivo no filtra suscripciones: sin este
   cambio un equipo dynsec **recibe el `cmd` de otro** si se suscribe a su topic.
3. **Mismo nombre en los dos mecanismos:** si se crea en dynsec un usuario que ya
   está en el passwordfile, manda dynsec (la clave vieja deja de servir). Sirve
   para migrar en la etapa 6; la API tiene que rechazar IDs que ya estén en el
   passwordfile (hoy `lab-esp8266-dell-01`; los `ius-`/`gw-` no chocan).
4. `disableClient` corta la conexión activa en el momento; cambiar la clave o
   borrar el usuario no requiere reiniciar. Usuarios, rol y defaults persisten al
   reiniciar el broker en `dynamic-security.json` (claves con hash, no en texto plano).
5. El client id sigue siendo el usuario (`use_username_as_clientid`).
6. Con o sin una línea `pattern` en `acl-public` el resultado es el mismo (no se
   pudo leer el archivo real: es de root).

### 3.4 Plataforma (API y base de datos)

> **Implementado en la etapa 2** con dos cambios respecto de esta propuesta:
> `claimed_at`/`claimed_by` quedó como `company_since` (se marca en reclamo,
> asignación manual y movida; quién lo hizo va a la auditoría), y las rutas de
> superadmin son `POST /api/admin/devices/{id}/release`,
> `PUT /api/admin/devices/{id}/mqtt` y la asignación ya existente
> `PUT /api/admin/devices/{id}`. Detalle en [alta-automatica-equipos.md](alta-automatica-equipos.md).

Datos nuevos por equipo (en `devices` o en una tabla aparte):

| Campo | Uso |
|---|---|
| `model` | `ius-device`, `gateway-wiegand`, … |
| `provisioned_at` | cuándo se fabricó |
| `claim_code_hash` | hash del código de reclamo (nunca el código) |
| `claimed_at`, `claimed_by` | quién y cuándo lo reclamó |
| `mqtt_enabled` | si el usuario MQTT está habilitado |
| `company_id` | ya existe en la rama multiempresa; `NULL` = sin empresa |

Endpoints:

| Endpoint | Quién | Qué hace |
|---|---|---|
| `POST /api/factory/devices` | herramienta de fábrica (token propio) o superadmin | registra el equipo sin empresa, crea su usuario MQTT y devuelve secreto + código **una sola vez** |
| `POST /api/devices/claim` | **solo** el administrador de una empresa (§9) | con ID + código válido y sin usar, asigna el equipo a su empresa |
| `PUT /api/devices/{id}/company` | superadmin | asigna o mueve el equipo a mano |
| `POST /api/devices/{id}/release` | superadmin | lo deja sin empresa y genera un código de reclamo nuevo (etiqueta nueva) |
| `POST /api/devices/{id}/disable` | superadmin | deshabilita su usuario MQTT (robo, baja) |

Reglas:
- Límite de intentos de reclamo por usuario y por IP; todos van al log de auditoría.
- Un equipo sin empresa solo lo ve el superadmin (es lo que ya hace
  `device_in_scope()` con `company_id` vacío).
- Un reclamo con un código ya usado falla igual que uno inválido, sin decir si
  el equipo existe.

### 3.5 Herramienta de fábrica (en la notebook)

Script `tools/fabricar-equipo.py` (en el repo del firmware o en iot-platform):

1. Lee el chip ID del ESP por USB (`esptool chip_id`).
2. Llama a `POST /api/factory/devices` y recibe secreto y código de reclamo.
3. Carga el firmware y le pasa el ID y el secreto **por el puerto serie**: un
   comando `PROVISION` que el firmware acepta solo si todavía no tiene
   identidad y solo por USB. El firmware lo guarda en LittleFS (`/device.json`).
4. Verifica en la plataforma que el equipo se conectó (`status` = `online`).
5. Genera la etiqueta con QR (PNG/PDF) para imprimir.

El secreto no queda escrito en la notebook ni en ningún archivo: pasa de la
respuesta de la API al puerto serie.

### 3.6 Firmware

Cambios en `esp12_serial_ius` (sería la 1.3.0) y luego en el gateway:

- Leer ID y secreto de `/device.json` en vez de `config.h`; `config.h` queda
  solo para valores de desarrollo.
- Comando serie `PROVISION <id> <secreto>` (una vez) y `IDENTITY` (muestra el
  ID, nunca el secreto).
- Mostrar el ID en el monitor y en la página del modo AP.
- Sin identidad: funciona sin MQTT, como hoy sin credencial.

### 3.7 Dashboard

- **Agregar equipo:** abre la URL del QR (o cámara dentro de la página), pide
  confirmar empresa y nombre del equipo, y llama a `claim`.
- **Equipos sin empresa** (superadmin): lista los equipos fabricados y
  conectados que todavía no tienen dueño, con asignar, liberar y deshabilitar.
- **Reimprimir etiqueta:** genera un código nuevo (invalida el anterior).

## 4. Etapas

| # | Etapa | Resultado esperado | Quién |
|---|---|---|---|
| 0 | Ordenar `/srv/iot-platform`: llevar a `master` solo la parte de empresas (`company_id`, `tenancy`) | multiempresa de equipos versionada y desplegable. **2026-11-07: rama `feature/empresas-equipos` probada en aislado; ver [multiempresa-plataforma.md](multiempresa-plataforma.md)** | Claude + Hugo (ya pendiente en T-20261105-03) |
| 1 | Prueba aislada del broker con Dynamic Security (contenedor descartable, sin red pública, como `docs/test-mqtt-tls.py`) | se confirman los tres puntos de §3.3. **2026-11-07: hecha, 37/37; ver resultado en §3.3** | Claude |
| 2 | API: campos, endpoints y cliente de Dynamic Security, con pruebas | alta, reclamo, asignación, liberación y baja probadas con broker de prueba. **2026-11-07: hecha en `feature/alta-equipos`, ver [alta-automatica-equipos.md](alta-automatica-equipos.md)** | Claude |
| 3 | Firmware 1.3.0 + herramienta de fábrica | ESP-12 fabricado de punta a punta contra el entorno de prueba | Claude + Hugo (USB) |
| 4 | Dashboard: agregar equipo, sin empresa, etiqueta | reclamo por QR desde el celular | Claude |
| 5 | Producción: habilitar Dynamic Security en 8883 junto al `passwordfile` actual | equipos nuevos se dan de alta solos; el laboratorio sigue conectado | Hugo corre los pasos de root; Claude verifica |
| 6 | Migrar `lab-esp8266-dell-01` y el gateway a Dynamic Security; después retirar el `passwordfile` público | un solo mecanismo de usuarios | Claude + Hugo |

Cada etapa se documenta y se aprueba antes de la siguiente. Los cambios del
broker de producción (5 y 6) llevan backup previo de la configuración y de los
secretos, y un paso de vuelta atrás.

## 5. Migración sin cortes

- El equipo de laboratorio y el gateway siguen con su usuario actual del
  `passwordfile` hasta la etapa 6.
- Si la etapa 1 muestra que los dos mecanismos no pueden convivir en el mismo
  listener, la alternativa es un listener 8884 nuevo para equipos fabricados y
  migrar los existentes cambiando solo el puerto en su firmware.

## 6. Seguridad

- Secretos por equipo: si un equipo se roba, se deshabilita solo ese usuario.
- El token de fábrica es distinto de los usuarios del dashboard, se usa solo
  desde la notebook y se puede revocar.
- Ni el secreto ni el código de reclamo se guardan en texto plano en la base
  ni en logs.
- La cuenta de administración de Dynamic Security no se publica a internet.

## 7. Fuera de alcance por ahora

- Certificados de cliente por equipo (mTLS): más seguro, pero pesado para el
  ESP8266 y más complejo de fabricar.
- Configurar el WiFi desde la app como las cámaras (sin portal): se puede
  agregar después; hoy se usa el portal del modo AP.

## 8. Archivos relacionados

- Firmware ESP-12: `github.com/huguitor/esp12_serial_ius` (1.2.0, MQTT TLS).
- Gateway: `~/Arduino/esp8266_gateway_wiegand_mqtt` en la Dell.
- Broker TLS: `docs/despliegue-mqtt-tls-20261104.md`,
  `docs/verificacion-acl-renovacion-mqtt.md`, `mosquitto/config/mosquitto-tls.conf`.

## 9. Decisiones de Hugo

Tomadas el 2026-11-07:

1. **Formato del ID:** `ius-<chipid>` para los Ius-device y `gw-<chipid>` para
   los gateways (chip ID del ESP en hexadecimal, minúsculas, 8 dígitos).
2. **Datos previos al reclamo:** la empresa **no** los ve; ve solo desde el
   momento del reclamo (consultas filtradas por `claimed_at`).
3. **Quién reclama:** solo el **administrador** de la empresa.

4. **Herramienta de fábrica:** en **iot-platform** (sirve para todos los modelos
   y habla con la API de la plataforma).
