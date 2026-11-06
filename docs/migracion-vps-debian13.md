# Preparación para VPS Debian 13

Esta preparación no inicia contenedores ni ejecuta migraciones. La instalación
original del Debian local permanece intacta. Todos los pasos operativos siguientes
son para una ventana futura autorizada, exclusivamente en `/srv/iot-platform` del VPS.

## Cambios

- Compose usa el proyecto `iot-platform` y una red bridge propia `iot_private`
  (por defecto `iot-platform_iot_private`), creada por Compose, sin `external`.
  Es una red separada de Camera Platform; permite salida normal de los contenedores.
- PostgreSQL 15, Redis 7 y Mosquitto 2 conservan imágenes, comandos, persistencia,
  autenticación, ACL y puertos internos. PostgreSQL y Redis no publican puertos.
- API y dashboard publican exclusivamente en IPv4 loopback.
- El nginx del dashboard sirve React con fallback de rutas y reenvía `/api/` a la
  API por la red del proyecto. `VITE_API_BASE=/api` evita depender del dominio o
  puerto y se aplica al construir el frontend.
- `mqtt-worker` requiere el perfil `worker`. No activar ese perfil ni seleccionar
  explícitamente ese servicio durante la migración: seleccionarlo por nombre
  también permite iniciarlo aunque no se indique el perfil.

| Servicio | Variable del puerto host | Valor propuesto | Puerto interno |
| --- | --- | --- | --- |
| API | `IOT_API_PORT` | `127.0.0.1:18000` | 8000 |
| Dashboard | `IOT_DASHBOARD_PORT` | `127.0.0.1:18080` | 80 |
| MQTT TCP | `IOT_MQTT_PORT` | `127.0.0.1:1883` | 1883 |
| MQTT WebSocket | `IOT_MQTT_WS_PORT` | `127.0.0.1:9001` | 9001 |

Camera Platform declara `127.0.0.1:8000` en su `compose.yml`; los puertos web
propuestos no coinciden. Verificar disponibilidad real de los cuatro puertos
con `ss -ltn` en el VPS antes del despliegue. Si alguno está ocupado, cambiar
su variable `IOT_*` sin modificar Camera Platform. `API_PORT` y
`MQTT_BROKER_PORT` son valores internos y no deben cambiarse por ese motivo.

## Procedimiento de migración futura

### Arranque seguro de la API

Para la prueba de migración, definir `API_STARTUP_READ_ONLY=true` en el `.env`
del VPS antes de crear el contenedor de API. Se aceptan `1`, `true`, `yes` y `on`,
sin distinguir mayúsculas ni espacios al principio/final. El valor predeterminado
es `false`; cualquier otro valor conserva el arranque normal.

Con la variable activada, `on_startup()` retorna sin ejecutar
`Base.metadata.create_all()`, los seis `ALTER TABLE`, el `CREATE INDEX` ni
`auth.ensure_admin_user()`. No crea ni modifica el administrador inicial.
Con la variable desactivada, se conserva el comportamiento anterior completo.

Este modo sólo evita escrituras de inicialización: no vuelve de sólo lectura a
los endpoints ni a PostgreSQL. Login, sesiones, auditoría y solicitudes de comandos
pueden seguir escribiendo. Tampoco valida ni corrige el esquema restaurado;
verificar su compatibilidad previamente y mantener el worker apagado.
Cambiar la variable requiere recrear el contenedor en la ventana autorizada;
un simple reinicio no actualiza su entorno.

Pruebas unitarias sin servicios ni conexiones a la base:

```bash
python3 -m unittest discover -s api/tests -v
```

### Pasos

1. Verificar Docker Engine y el plugin Docker Compose en Debian 13. No instalar,
   reiniciar ni reconfigurar servicios compartidos como parte de esta preparación.
2. Trabajar en una copia independiente en el VPS. No ejecutar comandos de Compose
   sobre la instalación local ni reutilizar sus directorios de datos en vivo.
   Confirmar que no exista otro proyecto o contenedor `iot-platform` en el VPS.
3. Preparar `.env` de forma privada a partir de `.env.example`, reemplazando todos
   los valores de ejemplo mediante el procedimiento de secretos autorizado.
   Mantener los hosts internos `postgres`, `redis` y `mosquitto`, y las contraseñas
   coherentes entre PostgreSQL y las URLs. No versionar secretos ni dumps.
   Esta preparación no incluye ni crea credenciales reales.
4. Planificar un respaldo consistente de PostgreSQL y Redis, y de la persistencia
   de Mosquitto, junto con ACL y archivo de contraseñas. Transferencia y restauración
   requieren su propia ventana autorizada. No copiar un PGDATA activo; preferir
   dump/restauración compatibles con PostgreSQL 15. Verificar propietarios y permisos
   de los bind mounts del VPS. El archivo `mosquitto/config/passwordfile` debe existir
   como archivo antes de arrancar; el ejemplo no habilita autenticación real.
5. Validar sin iniciar servicios:

   ```bash
   cd /srv/iot-platform
   docker compose --env-file .env config --quiet
   docker compose --env-file .env config --services
   ```

   Deben aparecer mosquitto, postgres, redis, api y dashboard; el worker debe quedar
   fuera. Verificar que `COMPOSE_PROFILES` no incluya `worker`. Evitar imprimir
   `docker compose config` completo porque expande secretos.
6. Durante la ventana autorizada, restaurar los datos en los directorios exclusivos
   del VPS antes de iniciar consumidores. Conservar los originales y los respaldos.
   Configurar `API_STARTUP_READ_ONLY=true` antes de iniciar la API y verificar
   compatibilidad del esquema y existencia del usuario previsto. El arranque normal
   crea tablas, altera columnas y asegura el administrador (`api/app/main.py`);
   mantenerlo desactivado durante esta prueba. Cualquier migración Alembic necesaria
   debe revisarse y autorizarse por separado.
7. Una vez autorizados restauración y arranque con el modo seguro, iniciar solamente
   los cinco servicios indicados, sin worker:

   ```bash
   docker compose up -d postgres redis mosquitto api dashboard
   ```

   No usar `--profile worker`, `COMPOSE_PROFILES=worker` ni iniciar `mqtt-worker`.
   Comprobar logs y salud de PostgreSQL/Redis/Mosquitto antes de validar la API;
   `depends_on` ordena el arranque, pero no garantiza disponibilidad.
8. Probar desde el VPS el dashboard en `http://127.0.0.1:18080` y la documentación
   de API en `http://127.0.0.1:18000/docs`. Para un navegador remoto se puede usar
   un túnel SSH, ajustando usuario/host y puertos si corresponde:

   ```bash
   ssh -N -L 18080:127.0.0.1:18080 usuario@vps
   ```

   Abrir `http://127.0.0.1:18080` localmente. Validar autenticación y `/api/` desde
   ese mismo origen. MQTT sólo acepta pruebas locales o por túnel durante esta fase;
   los dispositivos remotos no se conectarán al broker del VPS todavía.
9. Mantener el worker apagado hasta un corte de ingesta aprobado que evite duplicar
   procesamiento con el Debian local. Publicación mediante proxy, DNS/TLS y acceso
   MQTT externo quedan para una etapa posterior autorizada.

## Reversión

Si la prueba futura falla, detener sólo los servicios de este proyecto en el VPS
con `docker compose stop`. Conservar datos y respaldos para diagnóstico, sin borrar
volúmenes ni directorios. La instalación original local sigue siendo el punto de
retorno; no modificar sus servicios ni los de Camera Platform.
