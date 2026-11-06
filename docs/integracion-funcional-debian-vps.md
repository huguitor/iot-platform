# Integración funcional Debian → VPS

> Actualización verificada 2026-11-05: el worker está activo y el dispositivo de
> laboratorio anuncia ESP8266 1.6.2; hay accesos autorizados/denegados persistidos.
> Ver [estado Arduino + MQTT + plataforma](integracion-arduino-wiegand-mqtt.md).
> Las referencias siguientes a worker pendiente/apagado son el registro histórico
> de la integración del 2026-11-04, no el estado operativo actual.

## Estado y alcance

Verificado 2026-11-04 por Codex en `vps-aca3e209-vps-ovh-us`:
**API y dashboard desplegados y verificados; worker integrado, pendiente de activar**.
Ver [resultados del despliegue](despliegue-api-dashboard-20261104.md).
Se compararon ambos árboles
con `bb5b568` y se aplicaron parches selectivos desde Debian por SSH de solo lectura.
No se modificó Debian ni se copiaron `.env`, credenciales, datos o persistencia.
Los cambios previos del VPS se conservaron; no hubo commit ni push.

La base activa registra `20261104_01`; la integración no ejecutó migraciones ni
escrituras contra ella. El despliegue posterior recreó solo API/dashboard y probó
autenticación, con sus escrituras normales de sesión/auditoría. El worker sigue
ausente; PostgreSQL, Redis y Mosquitto conservan IDs y horas de arranque.

## Archivos integrados y adaptaciones

| Archivos | Resultado |
| --- | --- |
| `api/app/models.py`, `api/app/schemas.py` | Cinco columnas ORM y cuatro campos públicos de firmware, compatibles con la migración aplicada. El JSONB `info` no se agrega a la respuesta pública. |
| `mqtt-worker/worker.py` | `/info`, `boot`, metadatos parciales, fechas de eventos, sesiones persistentes, reconexión, QoS 1, heartbeat y correcciones de integridad descritas abajo. |
| `dashboard/src/App.jsx` | Contraseña inicial vacía. |
| `dashboard/src/utils/time.js` | Utilitario nuevo; zona configurable, fallback UTC ante zona inválida y ciclo h23 para mostrar medianoche como 00:00. |
| `AccessControlPanel.jsx`, `AuditPanel.jsx`, `DeviceDetail.jsx`, `DeviceList.jsx`, `MqttEventsPanel.jsx` | Usan el utilitario común de fechas. |
| `docker-compose.yml` | Solo se añadieron healthcheck TCP y dependencia del worker respecto de Mosquitto saludable; se conservaron red privada, loopback, montajes y perfil opcional. |
| `mosquitto/config/acl` | Regla exacta de heartbeat para el usuario de servicio existente; las otras reglas y permisos de archivo se conservaron. Sin recarga del broker. |
| `.env.example` | Ejemplo de `MQTT_WORKER_CLIENT_ID` y contraseña de administrador como marcador de reemplazo; no se cambió `.env`. |
| `mqtt-worker/tests/test_worker.py`, `dashboard/tests/time.test.mjs` | Pruebas nuevas sin mensajes MQTT reales. |
| `docs/run-firmware-migration-tests.py` | Opción `--functional` para probar API, migración y worker en aislamiento. |

Se conservaron Nginx, `API_STARTUP_READ_ONLY` y la API de arranque del VPS.
No se copiaron la red `estarnet_network`, la exposición MQTT pública ni el Compose
completo de Debian. Tampoco se habilitó la cola QoS 0 de Debian ni se alteró
`mosquitto.conf`: esa política de colas requiere una evaluación propia.

## Integridad de eventos y metadatos

- Un evento lleva `received_at` y hora estimada `occurred_at` en su auditoría.
  `delay_s` debe ser un número finito entre cero y 30 días; valores inválidos
  quedan diagnosticados y no actualizan presencia ni estado actual.
- El historial conserva eventos atrasados y el payload original. Las actualizaciones
  del relé y sensor comparan sus respectivas fechas; los campos compartidos de
  lector/tipo también respetan la fecha más reciente del otro estado.
- Un mensaje retenido no acredita presencia ni reemplaza estado de acceso actual.
  Un evento atrasado tampoco marca presencia nueva. Redis se actualiza después
  de confirmar la transacción de eventos, evitando presencia fantasma por rollback.
- `/info` y `boot` fusionan metadatos parciales: omitir un campo no borra la versión
  o capacidad conocida. Se conservan payloads en `mqtt_events` y auditoría.
  La observación más vieja no reemplaza una más nueva. Sin una fecha fiable del
  emisor, `/info` retenido solo inicializa metadatos aún desconocidos; un anuncio
  nuevo no retenido puede actualizarlos. El anuncio retenido también queda registrado.
- `list`, `stats`, `stats_reset` y `boot` quedan en MQTT y auditoría, sin contaminar
  `access_events`. No se eliminan registros históricos existentes.
- Tipos/campos inválidos y JSON con números no finitos se registran como fallos,
  conservando evidencia del payload, sin provocar un bucle por inserciones inválidas.

### Identidad y duplicados

Para deduplicación persistente de `/event`, el firmware debe emitir:

```json
{"event":"access","boot_id":"identidad-exclusiva-del-arranque","event_id":1}
```

`boot_id` debe cambiar en cada arranque; `event_id` identifica un evento dentro de
ese arranque y se conserva en retransmisiones. La clave incluye también dispositivo
y tópico. Las conexiones se serializan por identidad mediante un advisory lock
transaccional; el recibo MQTT, auditoría y efectos de acceso se confirman juntos.
La deduplicación consulta recibos previos en `mqtt_events`, sin cambios de esquema.

Se conservan recibos y auditoría de duplicados; no se repiten efectos de acceso.
Si una identidad se reutiliza con contenido diferente, se registra un conflicto
y se conserva el original. `delay_s` puede cambiar al retransmitir sin generar
un conflicto de contenido.

**Límites:** sin ambos identificadores estables no se deduplica: mensajes iguales
pueden ser dos accesos reales. Ni el flag DUP ni el packet ID MQTT son identidad
de negocio. La deduplicación deja de cubrir un evento si se elimina su recibo
original; no establecer una retención de `mqtt_events` sin evaluar esto. Falta
medir el costo de búsqueda con históricos grandes antes de escalar; no se añadió
un índice ni una migración no autorizada.

### Sesiones, errores y heartbeat

El worker usa client ID estable configurable, `clean_session=False`, reconexión
con espera de 1 a 30 segundos y reintento de conexión inicial. `/event` se suscribe
con QoS 1. El heartbeat, cada 10 segundos y sin retain, solo se habilita tras un
SUBACK exitoso del tópico de eventos; se deshabilita al desconectar.

Ante un fallo de procesamiento/BD, el callback propaga el error en lugar de
confirmar un evento no persistido. Se comprobó con el parser real de Paho 1.6.1,
sin red, que no emite PUBACK si falla el callback. Al desplegar, la política
`restart: unless-stopped` del worker permitirá recuperar la sesión; esto implica
que una falla persistente necesita diagnóstico, no reintentos destructivos.

No se promete entrega exactamente una vez: el Arduino/gateway debe publicar
eventos con QoS 1 para obtener esa garantía de transporte, conservar identificadores
y disponer de cola ante desconexión según su firmware. No se modificó firmware.
El despacho saliente de comandos conserva el comportamiento previo; no se probó
accionamiento real ni se garantiza idempotencia del dispositivo para comandos.

## Pruebas y resultados

Verificado 2026-11-04 en VPS:

```bash
python3 -B docs/run-firmware-migration-tests.py --functional
node --test dashboard/tests/time.test.mjs
```

- **28 pruebas Python OK**, sin omisiones: 8 de migración/arranque, 9 unitarias
  del worker y 11 de integración con PostgreSQL descartable. Cubren ORM/esquemas,
  `/info`, `boot`, eventos atrasados, retención, duplicados entre conexiones y
  concurrentes, conflictos de identidad, rollback, auditoría y metadatos parciales.
- **2 pruebas de fechas OK**: zona argentina, medianoche 00:00, fechas inválidas
  y fallback UTC. La primera ejecución detectó el formato 24:00; quedó corregido.
- Compilación del frontend con `npm ci` y `npm run build`, Node 20, Vite 5.4.21,
  `VITE_API_BASE=/api` y zona argentina en un directorio temporal. Sin modificar
  package.json, lockfile ni imágenes activas. Resultado: compilación satisfactoria.
  Advertencias no bloqueantes: bundle JS de unos 656 kB, API CJS de Vite y versión
  de Recharts deprecadas. No se actualizaron dependencias fuera de esta integración.
- Compose validado; el worker sigue excluido de los cinco servicios predeterminados.
  Se verificó que `nc` existe en la imagen actual del broker; no se ejecutaron
  pruebas MQTT de autenticación, publicación ni suscripción.
- Consulta posterior de solo lectura: revisión activa `20261104_01`; coinciden
  las huellas de todos los datos anteriores de las 23 tablas distintas de
  `alembic_version`, y las columnas de firmware siguen en NULL. Los servicios
  mantienen sus IDs y horas de arranque originales; no existe contenedor worker.

El ejecutor funcional construye una imagen temporal con las dependencias del
worker, sin reemplazar etiquetas de servicio; la elimina al terminar. PostgreSQL
solo usa tmpfs y red `none`, sin puertos publicados. Redis y MQTT se simulan.
La carpeta API y la del worker se montan en solo lectura, sin `.env` del proyecto.
La suite no importa ni arranca Uvicorn y no se conecta a Debian. Las pruebas de
PostgreSQL destruyen únicamente fixtures de la base descartable protegida por el
ejecutor. No son pruebas end-to-end con un broker o Arduino.

## Requisitos y orden de despliegue

API/dashboard ya fueron construidos, recreados y verificados el 2026-11-04,
incluyendo login y exposición de firmware. Los pasos de broker, worker y Arduino
de esta lista continúan pendientes; ver el registro de despliegue enlazado arriba.

1. Revisar el diff y el estado real del VPS; conservar respaldo y verificar la
   revisión `20261104_01`. Mantener la protección de arranque de API y worker apagado.
2. Autorizar por separado construcción y recreación de API/dashboard para cargar
   este código. Verificar login, permisos y campos de firmware en el entorno de
   pruebas; esos endpoints pueden escribir sesiones/auditoría.
3. Programar la aplicación del healthcheck y ACL del broker. La edición de Compose
   no modifica contenedores existentes; la ACL preparada no fue recargada.
   El healthcheck acredita TCP abierto, no credenciales ni permisos MQTT.
4. Definir en la configuración privada un `MQTT_WORKER_CLIENT_ID` exclusivo y
   estable para el VPS, sin compartirlo entre workers conectados al mismo broker.
   El valor de `.env.example` no cambia por sí solo el entorno real.
5. Revisar el Arduino del taller: ID nuevo, tópico de heartbeat, firmware que
   publique eventos QoS 1 con `boot_id`/`event_id`, fechas y payloads compatibles.
   Preparar credenciales propias y ACL por dispositivo; las ACL amplias actuales
   se conservaron y todavía no vinculan estrictamente usuario MQTT con device_id.
   Resolver transporte protegido al broker que sigue publicado en loopback.
6. Autorizar construcción/arranque del worker mediante su perfil, tras revisar
   comandos pendientes, mensajes retenidos y colas. El bootstrap legado aún
   ejecuta `ensure_tables`; la ampliación de firmware ahora exige columnas previas
   y no las crea implícitamente. La activación merece su propia ventana.
7. Probar primero con simulador aislado y luego con el Arduino: corte/reconexión,
   retransmisión, persistencia, SUBACK rechazado y comportamiento de heartbeat.
   No activar simultáneamente workers con la misma identidad de cliente.

## Continuidad

Esta nota es el traspaso técnico de la integración preparada. Proponer registrar
la tarea y su enlace en `/srv/infra-docs/continuidad/TAREAS.md` y un traspaso
compartido, y revisar `infraestructura/ESTADO-INFRAESTRUCTURA.md` al desplegar.
La documentación compartida no se modificó en esta tarea.
