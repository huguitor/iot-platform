# Arquitectura del proyecto IoT Platform

## Estructura principal
- `api/`: backend FastAPI que expone la API REST.
  - `app/database.py`: conexión SQLAlchemy (`engine`, `SessionLocal`, `Base`) usando `DATABASE_URL` y dependencia `get_db()`.
  - `app/models.py`: define tablas `Device`, `Telemetry`, `Command`, `User`, `UserDevicePermission` con relaciones y JSONB para cargas.
  - `app/schemas.py`: DTOs Pydantic para validación/respuestas (dispositivos, telemetría, comandos, permisos, usuarios, tokens). `CommandCreate` exige `{ command: str }`.
  - `app/auth.py`: autenticación JWT + hashing (`bcrypt_sha256`, `pbkdf2_sha256`), `get_current_user`, `require_admin`, `ensure_admin_user`.
  - `app/main.py`: instancia FastAPI, configura CORS, Redis, rutas `/api/devices/*`, `/api/auth/token`, `/api/admin/*`, y maneja permisos (`can_read`, `can_write`). Los comandos se guardan con estado `pending`.

- `mqtt-worker/`: daemon que conecta MQTT ↔ Postgres y Redis.
  - `worker.py`: suscribe tópicos (`/telemetry`, `/status`, `/state`, `/response`), almacena telemetría/status, encola comandos pendientes y los publica directamente como JSON string en `iot/devices/{device_id}/cmd` cada 0.5 s, actualizando `status` en la DB y en Redis.

- `dashboard/`: aplicación React/Vite.
  - `src/api.js`: cliente Axios reutilizable; funciones `login`, `fetchDevices`, `fetchTelemetry`, `sendCommand` (stringifica cargas, envía `{ command: payload }`), gestores admin.
  - `src/main.jsx`: arranca `<App />` y aplica estilos globales.
  - `src/App.jsx`: controla estado de sesión/TOKEN, refresca dispositivos/telemetría periódicamente, selecciona vistas (dashboard/admin), pasa `handleSendCommand` a `DeviceDetail`.
  - Componentes clave:
    - `DeviceList`: listado/selección de dispositivo.
    - `DeviceDetail`: muestra métricas/cmds; ejecuta `onSendCommand` con mensajes preset o personalizados y maneja feedback.
    - `TelemetryChart`: grafica telemetría.
    - `TerminalFeed`: flujo de eventos.
    - `StatusBadge`: chip de estado.
    - `AdminPanel`: gestión de usuarios y permisos.

- `nginx/`: proxy frontal.
  - `default.conf`: expone `/docs`, `/openapi.json`, `/api/` y `/` (SPA). Añade CSP que permite fuentes de Google y scripts de Cloudflare, headers `X-Real-IP/X-Forwarded-*`, y maneja SPA (`error_page 404 = /index.html`).

- `docker-compose.yml`: define servicios (postgres, redis, mosquitto, api, mqtt-worker, dashboard, nginx) y dependencias/env vars. Cada servicio se rebuild para pickear cambios.
- `.env`/`.env.example`: variables (DB, Redis, MQTT, JWT, dashboard).

## Flujo general
1. El frontend inicia sesión (`/api/auth/token`) y usa el token para consultar `/api/devices` y `/telemetry`.
2. Cuando el usuario envía un comando, `dashboard` llama a `sendCommand` con un JSON stringificado. FastAPI valida permisos y guarda `Command(command=payload, status='pending')`.
3. `mqtt-worker` lee comandos `pending`, publica el texto en MQTT (`iot/devices/{id}/cmd`) y marca el registro como `sent`; también mantiene Redis con `device:{id}:status` y almacena telemetría entrante en PostgreSQL.
4. La SPA muestra datos enriquecidos con el status actual leído desde Redis y confía en la configuración de nginx para servir rutas y recursos externos.

## Recomendaciones para revisión y despliegue
- Antes de producir: reconstruir imágenes `api`, `dashboard`, `mqtt-worker` y `nginx` para incluir cambios y luego levantar `docker compose up -d`.
- Verificar logs tras el deploy (`docker compose logs --tail 20 api`, `mqtt-worker`) y confirmar que `POST /api/devices/{id}/command` responde 200.
- Si hay proxies externos, replicar la cabecera CSP de `nginx/default.conf` para evitar bloqueos de fuentes y scripts.
- Opcional: ejecutar el script interno (`POST /api/auth/token` + `POST /api/devices/mi-device/command`) dentro del contenedor API para verificar el flujo sin dependencias de UI.
