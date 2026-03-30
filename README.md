# IoT Platform (Docker Compose)

Plataforma IoT lista para escalar a cientos/miles de dispositivos. Incluye broker MQTT (Mosquitto), API REST (FastAPI), worker MQTT, PostgreSQL, Redis, dashboard React y Nginx como reverse proxy.

## Estructura de servicios
```
iot-platform
├── mosquitto        (broker MQTT)
├── api              (FastAPI REST)
├── mqtt-worker      (procesador MQTT)
├── postgres         (base de datos)
├── redis            (cache/estado)
├── dashboard        (React + Vite, servido con nginx)
└── nginx            (reverse proxy para API + dashboard)
```

## Configuración rápida
1. Copia variables: `cp .env.example .env` (ya se dejó un .env inicial).
2. Genera usuarios MQTT (obligatorio):
   ```bash
   cd mosquitto/config
   docker run --rm -v "$PWD":/mosquitto/config eclipse-mosquitto:2 \
     mosquitto_passwd -b /mosquitto/config/passwordfile iotuser changeme
   ```
   - Ajusta `iotuser/changeme` al valor de `MQTT_USERNAME` y `MQTT_PASSWORD` en `.env`.
3. Levanta todo: `docker compose up -d` (desde la raíz del proyecto).
4. Dashboard: http://localhost (Nginx sirve dashboard y proxy a /api).

## Topics y payloads
- Telemetría: `iot/devices/{device_id}/telemetry` payload JSON `{ "ts": 1710000000, ... }`
- Estado: `iot/devices/{device_id}/status`
- Comando: `iot/devices/{device_id}/command` (publicado por worker desde DB)
- Respuesta: `iot/devices/{device_id}/response`

## Endpoints REST principales
- `POST /api/auth/token` (JWT): body `username`, `password`.
- `GET /api/devices` (admin ve todo; usuario solo dispositivos con permiso de lectura).
- `GET /api/devices/{device_id}` (mismas reglas de lectura).
- `GET /api/devices/{device_id}/telemetry` (mismas reglas de lectura; filtros `start/end/limit`).
- `POST /api/devices/{device_id}/command` (admin o usuarios con `can_write` en el dispositivo).
- Admin:
  - `POST /api/admin/users` crea usuario (`username`, `password`, `role`=`admin|user`).
  - `PUT /api/admin/users/{user_id}/permissions` asigna/actualiza permisos por dispositivo (`device_id`, `can_read`, `can_write`).
- Dashboard incluye un panel “Usuarios y permisos” (requiere token de admin) para crear usuarios y asignar permisos sin usar cURL.

## Notas de seguridad
- Al iniciar la API se crean tablas y, si no existe, se genera el usuario admin con `API_ADMIN_USER/API_ADMIN_PASSWORD` del `.env` (password se guarda hasheada con bcrypt).
- JWT configurable via `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRES_MINUTES`.
- Mosquitto con auth + ACL (`mosquitto/config/acl`).
- Variables sensibles solo en `.env` (no se commitean en `.env.example`).

## Migración a /var/www
No pude crear `/var/www/iot-platform` sin privilegios sudo. Copia este directorio (`/home/hugo/iot-platform`) a `/var/www` o ajusta permisos y vuelve a ejecutar:
```
sudo mkdir -p /var/www/iot-platform
sudo chown -R $USER:$USER /var/www/iot-platform
cp -r ~/iot-platform /var/www/
```

## Próximos pasos opcionales
- Agregar tests de API (Pytest) y pipelines.
- Añadir métricas/observabilidad (Prometheus + Grafana).
- Incorporar coordenadas para pintar mapa en dashboard.
