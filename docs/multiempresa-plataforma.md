# Multiempresa de la plataforma (empresas, equipos y usuarios)

- **Estado:** rama `feature/empresas-equipos`, probada en entorno aislado. **No
  desplegada.** Producción sigue en la revisión `20261104_01`.
- **Origen:** parte de plataforma rescatada de la rama `wip/multiempresa-acceso`
  (2026-11-05). La reescritura del módulo `access_control` de esa rama **no** se
  trae: ese trabajo pasó a la app Control de Acceso.
- **Plan relacionado:** [alta automática de equipos y reclamo por QR](plan-alta-automatica-equipos.md)
  (esta es su etapa 0).

## Reglas

| Rol | Empresa | Qué ve |
|---|---|---|
| superadmin | ninguna | Todo. Con el encabezado `X-Company-Id: <id>` trabaja con una empresa. Es el único que ve equipos **sin empresa**. |
| admin | la suya | Equipos, usuarios (`role=user`), auditoría y eventos de su empresa. El encabezado se ignora. |
| user | la suya | Equipos de su empresa con permiso de lectura. |

- Un equipo nuevo que llega por MQTT (lo crea el worker) queda con
  `company_id` vacío: **sin empresa**, visible solo para el superadmin.
- Fuera de su empresa un equipo "no existe" (404), también para comandos.
- Solo el superadmin crea y edita empresas, y asigna o mueve equipos. El admin
  de la empresa puede cambiar el nombre de sus equipos.
- Al mover un equipo de empresa se borran los permisos de usuarios sobre él.
- Los usuarios de una empresa deshabilitada no pueden entrar (login y token).
- **Cambio de comportamiento:** antes un admin veía solo los usuarios que había
  creado él; ahora ve todos los `user` de su empresa.
- El permiso de un usuario sobre un equipo exige que ambos sean de la misma empresa.

## API nueva

| Método y ruta | Quién | Qué hace |
|---|---|---|
| `GET /api/admin/companies` | todos | superadmin: todas; el resto: la propia |
| `POST /api/admin/companies` | superadmin | crea (`name`, `code`, datos de contacto) |
| `PUT /api/admin/companies/{id}` | superadmin | edita; `is_active=false` deshabilita |
| `PUT /api/admin/devices/{device_id}` | superadmin / admin | superadmin: `{"set_company": true, "company_id": N \| null}`; ambos: `name` |
| `POST /api/admin/users` | superadmin / admin | ahora acepta `company_id` (superadmin); el admin crea en su empresa |

Las rutas existentes de equipos, telemetría, comandos, usuarios, auditoría,
eventos de acceso y eventos MQTT filtran por empresa. La API de integración
(`/api/integrations/access/*`) no cambia.

## Base de datos (migración `20261107_01`)

- Crea `companies` y la empresa **Panozo Sistemas** (`panozo`).
- `company_id` (nullable) en `devices`, `users`, `audit_logs`, `access_events`.
- Asigna a Panozo todos los equipos y los usuarios que no son superadmin; eventos
  y auditoría toman la empresa del equipo o del usuario.
- Triggers: `access_events` y `audit_logs` completan la empresa al insertar, así
  el **worker no cambia**.
- No toca las tablas `access_*` del módulo viejo de control de acceso.
- Reemplaza a la revisión `20261105_01` de la rama wip, que nunca se aplicó. Si
  ese archivo sigue sin commit en otro árbol, **no** correr Alembic desde ahí
  (habría dos revisiones "head").
- **Correrla una sola vez**: en una segunda corrida forzada, los equipos que para
  entonces estén sin empresa pasarían a Panozo.
- `downgrade` deshabilitado. Volver atrás **no** requiere restaurar la base: la
  imagen anterior de la API ignora las columnas nuevas (todas nullable).

Producción el 2026-11-07 (consulta de solo lectura): revisión `20261104_01`, sin
`companies`; 8 equipos; usuarios: 1 superadmin, 2 admin, 4 user.

## Pruebas

```bash
docs/run-empresas-tests.sh            # base: master
```

Levanta PostgreSQL y la API **descartables, sin red** (comparten el namespace de
red del contenedor de la base), con la imagen activa de la API y el código de
la rama montado en solo lectura:

1. Esquema como producción con el código de `master` (`create_all` + migraciones
   livianas), datos sintéticos y `alembic stamp 20261104_01`.
2. `alembic upgrade head` con la rama, segunda corrida (idempotencia) y
   verificación del esquema y de los triggers.
3. API con `API_STARTUP_READ_ONLY=true`, como producción.
4. `api/tests/empresas/test_empresas.py`: 10 pruebas HTTP (visibilidad por rol y
   empresa, asignación, comandos, usuarios, permisos cruzados, auditoría y
   eventos, empresa deshabilitada, integración, módulo viejo solo superadmin).
5. `api/tests/live_access_integration.py`: 4 pruebas existentes.
6. `tests.test_startup`.

Resultado 2026-11-07: **todo OK** (10 + 4 + unitarias), sin errores en el log de
la API. Los contenedores se borran al terminar.

## Despliegue (pendiente, requiere autorización)

Construir **siempre desde un checkout limpio de esta rama o de `master`**, nunca
desde `/srv/iot-platform` mientras esté en `wip/multiempresa-acceso`.

1. Backup de la base de iot-platform (pg_dump) y etiqueta de la imagen activa
   de la API para volver atrás.
2. Construir la imagen nueva de la API desde el checkout limpio.
3. Correr la migración con la imagen nueva:
   `python -m alembic upgrade head` (la API de producción arranca en modo
   solo lectura y no crea columnas sola).
4. Recrear solo el servicio `api`. El worker y el broker no cambian.
5. Verificar: `/api/health`, login de un admin, que vea sus 8 equipos, y que la
   app Control de Acceso siga leyendo eventos.

El orden importa: si se recrea la API nueva antes de migrar, las consultas a
`devices.company_id` fallan.

## Limitaciones conocidas

- **Dashboard:** todavía no tiene pantallas de empresas ni selector para el
  superadmin. Funciona igual: el superadmin ve todo y cada admin ve lo suyo.
- **Módulo viejo `access_control`** (`/api/access-control/*`, personas y
  tarjetas dentro de iot-platform): no filtra por empresa, así que desde esta
  rama queda **solo para el superadmin** (decisión de Hugo, 2026-11-07). Ni el
  dashboard ni la app Control de Acceso lo usan, y no tuvo pedidos en los 7 días
  previos. `/api/access/events` (eventos de la plataforma) sigue para todos,
  filtrado por empresa. Personas y tarjetas viven en la app Control de Acceso.
- La clave de la API de integración ve todos los equipos (pendiente ya anotado
  en `docs/integracion-control-acceso.md`).
