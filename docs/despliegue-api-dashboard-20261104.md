# Despliegue y verificación de API/dashboard — VPS

Verificado 2026-11-04 por Codex en `vps-aca3e209-vps-ovh-us`, con autorización
explícita del usuario. Proyecto `/srv/iot-platform`, base Git `bb5b568` con cambios
sin commit. **API y dashboard desplegados y verificados; worker pendiente y apagado.**

## Alcance ejecutado

Se revisó [la integración funcional](integracion-funcional-debian-vps.md), Git,
Compose efectivo, estado de contenedores y base activa. Antes del despliegue se
comprobó mediante lectura que la credencial configurada correspondía a una cuenta
existente y activa, sin mostrar contraseñas ni hashes.

Se mantuvieron red privada `iot_private`, puertos loopback, proxy Nginx y
`API_STARTUP_READ_ONLY=true`. El worker no aparece en los servicios predeterminados
y no existió ningún contenedor suyo durante la operación.

Único ajuste de código de esta etapa: `dashboard/Dockerfile` ahora copia también
`package-lock.json` y ejecuta `npm ci`, para construir con el lockfile probado.
No se cambiaron versiones de dependencias ni el lockfile.

```bash
docker compose build api dashboard
docker compose up -d --no-deps --no-build --force-recreate api dashboard
```

No se ejecutaron otros comandos de arranque, migraciones ni restauraciones.
PostgreSQL, Redis y Mosquitto conservaron exactamente sus IDs y horas de arranque.
La ACL y el healthcheck preparados para Mosquitto no se recargaron ni aplicaron
mediante recreación del broker. No se accedió a Debian local.

## Contenedores e imágenes verificados

Ambos contenedores web arrancaron a las **17:03:59 UTC (14:03:59 UTC-03:00)**.
Los cinco servicios estaban running, con cero reinicios, a las 17:06:08 UTC.

| Servicio | Contenedor | Imagen nueva (prefijo SHA-256) |
| --- | --- | --- |
| API | `c55e39730275` | `2a4bc683da5a` |
| Dashboard | `7b2d6ce8d200` | `b3ab929cb7c3` |

Imágenes anteriores observadas: API `f61af964b64a`, dashboard `3733c78b9353`.
No se eliminaron imágenes anteriores ni se intentó rollback.

Dependencias intactas, iniciadas el 2026-11-03 a las 21:33 UTC:
PostgreSQL `82e510aadcaf`, Redis `3c8b560f0c44`, Mosquitto `aacd2342a0cc`.

## Verificaciones funcionales

Todas las comprobaciones siguientes pasaron:

| Comprobación | Resultado |
| --- | --- |
| `http://127.0.0.1:18000/api/health` | HTTP 200, status ok |
| `http://127.0.0.1:18000/openapi.json` | HTTP 200; esquema Device contiene los cuatro campos públicos de firmware |
| HTTPS del VPS con SNI del dominio y conexión a loopback | Certificado validado, dashboard y proxy `/api/health` HTTP 200 |
| `https://iot.panozoelectronica.com.ar/` por resolución pública normal | HTTP 200; HTML idéntico al del nuevo contenedor |
| JS y CSS referenciados por el HTML público | Ambos HTTP 200; contenido idéntico al desplegado |
| Login HTTPS con una cuenta existente | HTTP 200; token mantenido solo en memoria, sin imprimirlo ni guardarlo |
| `/api/auth/me` autenticado | HTTP 200; identidad coincide con la cuenta usada |
| `/api/devices` autenticado | HTTP 200; siete dispositivos, con `fw_version`, `gateway_fw_version`, `card_capacity` e `info_at` |
| Logout HTTPS | HTTP 200 |

`info` es JSONB interno y no se expone en el esquema público de Device, conforme
a la integración. No se simuló firmware mediante UPDATE ni se enviaron comandos.
La prueba de login/lectura/logout generó las escrituras normales de sesiones y
auditoría; no se borraron esos registros de prueba.

## Integridad y logs posteriores

Consulta de PostgreSQL con transacción de solo lectura:

- `alembic_version` continúa en `20261104_01`.
- Las cinco columnas conservan tipo, longitud, nulabilidad y ausencia de DEFAULT.
- Coinciden recuentos y huellas de todas las filas de las **22 tablas distintas
  de `user_sessions` y `audit_logs`**, comparadas con la lectura consistente previa.
  Esto incluye todos los datos de dispositivos, usuarios, permisos, telemetría,
  MQTT y comandos. Solo se exceptuaron las dos tablas que escribe la autenticación.

Se revisaron los logs completos de los nuevos contenedores tras las pruebas:
sin tracebacks ni severidades ERROR/CRITICAL/FATAL de aplicación o Nginx.
El entrypoint del dashboard emitió un aviso informativo al no poder modificar
`default.conf`, montado en solo lectura. Nginx arrancó y sirvió correctamente;
no se cambiaron permisos ni se retiró el montaje para ocultar ese aviso.

Registros operativos de esta sesión, sin credenciales ni tokens:
`/tmp/iot-web-deploy-20261104T170242Z/`. Incluyen manifiestos de servicios,
huellas de datos, build/recreación y verificaciones. Es una ubicación temporal;
este documento conserva los resultados relevantes sin depender de su permanencia.

## Incidencias y pendientes

No falló ninguna validación del despliegue. La construcción terminó correctamente,
con advertencias que no se corrigieron automáticamente:

- npm informó **5 vulnerabilidades: 1 moderada y 4 altas** en el árbol instalado.
  Falta evaluar los avisos concretos, alcance runtime/build y una actualización
  probada; no se ejecutó `npm audit fix` ni se modificó el lockfile.
- Recharts 2 y la API CJS de Vite informaron deprecaciones; el bundle principal
  sigue superando 500 kB. No impidieron construir ni servir la aplicación.

Quedan fuera de este despliegue: activar worker, aplicar la configuración preparada
del broker, conectar Arduino y comprobar MQTT end-to-end. Requieren autorización
separada y las precauciones de la documentación de integración.

No hubo modificaciones a Debian, migraciones adicionales, comandos MQTT, cambios
de datos de dispositivos, commit ni push. Los cambios previos del repositorio
se conservaron.

## Continuidad compartida

Se propone registrar el despliegue y enlazar este documento desde un traspaso y
`/srv/infra-docs/continuidad/TAREAS.md`, y actualizar la instantánea
`/srv/infra-docs/infraestructura/ESTADO-INFRAESTRUCTURA.md` con la revisión de BD
y el estado web desplegado. Esos archivos compartidos no se editaron en esta etapa.
