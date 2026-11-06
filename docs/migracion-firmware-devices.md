# Migración de metadatos de firmware de devices

## Estado y alcance

Verificado 2026-11-04 en VPS `vps-aca3e209-vps-ovh-us` por Codex:
**aplicada a la base activa y verificada**, con autorización explícita del usuario.
La preparación y las pruebas previas se conservan abajo como historial.
Repositorio base: `bb5b568`, con cambios previos sin commit conservados.
No se modificó Debian local ni se activó el worker MQTT.

Nueva revisión: `20261104_01`, posterior a `20260818_01`.
Archivo: `api/alembic/versions/20261104_01_device_firmware.py`.

| Columna en public.devices | Tipo | Nullable | DEFAULT |
| --- | --- | --- | --- |
| fw_version | VARCHAR(50) | Sí | Ninguno |
| gateway_fw_version | VARCHAR(50) | Sí | Ninguno |
| card_capacity | INTEGER | Sí | Ninguno |
| info | JSONB | Sí | Ninguno |
| info_at | TIMESTAMPTZ | Sí | Ninguno |

La migración no importa el worker ni inicia la API. No contiene UPDATE, DELETE,
backfill, DROP, cambios de permisos ni índices. Agrega únicamente las columnas
faltantes y actualiza la revisión mediante Alembic, dentro de su transacción DDL.
Los registros anteriores quedan con NULL en las nuevas columnas.

## Compatibilidad con Debian y seguridad

La revisión inspecciona las columnas existentes antes de emitir cualquier ALTER.
Si ya tienen el tipo exacto previsto, admiten NULL y no tienen DEFAULT, las conserva
con sus valores. También acepta que solo exista una parte de las cinco columnas.
Una definición incompatible aborta sin convertir tipos ni corregir datos.

Histórico, comprobado en el diagnóstico anterior de esta conversación, no vuelto
a consultar en esta preparación: Debian tiene las cinco columnas y ambas bases
registran `20260818_01`. La prueba reproduce ese esquema con datos sintéticos;
no conecta con Debian ni certifica su estado futuro.

Requiere PostgreSQL online y la tabla `public.devices` existente. No usar generación
SQL offline (`--sql`): la decisión depende de inspeccionar el esquema real.
Se fijan `lock_timeout=5s` y `statement_timeout=60s`, locales a la transacción.
ALTER TABLE requiere un bloqueo exclusivo, aunque las columnas sean nullable y
sin DEFAULT; programar una ventana breve y evitar ejecutores concurrentes.

El downgrade está bloqueado expresamente: no se puede distinguir qué columnas
preexistían en Debian, y eliminarlas podría destruir datos. Una recuperación
requiere evaluación y autorización separadas; no usar `stamp` para simularla.

## Prueba reproducible en PostgreSQL descartable

Desde `/srv/iot-platform`:

```bash
python3 -B docs/run-firmware-migration-tests.py
```

El ejecutor usa las imágenes locales por ID: PostgreSQL 15 y la imagen ya asociada
a `iot-platform-api` para disponer de SQLAlchemy/Alembic. No descarga, reconstruye,
reinicia ni ejecuta pruebas dentro del contenedor activo. No carga `.env` ni copia
datos reales. El proceso de pruebas arranca Python unittest, nunca Uvicorn.

PostgreSQL se crea con red `none`, sin puertos publicados, con PGDATA en tmpfs.
El ejecutor comparte únicamente el namespace de red de ese contenedor y monta
`api/` en solo lectura. No tiene acceso a la red del proyecto ni al socket Docker.
La autenticación trust se limita a esta instancia aislada con datos sintéticos.
Se establecen límites de memoria, CPU y procesos. Ambos contenedores tienen nombres
UUID exclusivos y se eliminan al finalizar, incluidos fallos normales de pruebas.
Ante interrupción forzada del ejecutor, comprobar posibles contenedores
`iot-firmware-test-*` antes de dar por terminada la limpieza.

La suite rechaza una URL diferente de la base de prueba y requiere la variable
`IOT_MIGRATION_TEST_DISPOSABLE=1`. En esa base únicamente, reinicia `public` para
cada caso. No habilitar esa variable manualmente fuera del ejecutor.
La revisión previa se marca sobre fixtures; no se ejecuta su migración histórica
de usuarios ni se pretende recrear toda la plataforma desde cero.

### Resultados verificados 2026-11-04 en VPS

`python3 -B docs/run-firmware-migration-tests.py`: **8 pruebas OK**, sin omisiones:

- VPS: agrega las cinco columnas con tipos, NULL y DEFAULT correctos; conserva
  filas e IDs y una asociación de usuario/dispositivo con claves foráneas.
- Debian simulado: conserva las cinco columnas y valores existentes; repetir
  el upgrade a la misma revisión no modifica los registros.
- Esquema parcial: agrega solo lo faltante y conserva JSONB existente.
- Definiciones incompatibles: rechaza longitud, tipo, NOT NULL o DEFAULT;
  no agrega otras columnas y conserva la revisión anterior.
- Incompatibilidad en la última columna: valida todo antes de agregar la primera.
- Downgrade: rechaza la operación y conserva los datos y la revisión nueva.
- Dos pruebas de arranque existentes: pasan con inicialización omitida o normal.

`python3 -B -m unittest discover -s api/tests -v` en el host: dos pruebas de
arranque OK; seis de integración omitidas deliberadamente sin el ejecutor aislado.
Sintaxis de los tres archivos Python nuevos validada mediante `ast.parse`, sin
generar bytecode en el repositorio.

Comprobación posterior de solo lectura mediante la conexión de la API:
`alembic_version` sigue en `20260818_01`; ninguna de las cinco columnas existe
todavía en la base activa. Los cinco servicios siguen iniciados el 2026-11-03
a las 21:33 UTC; no hay contenedor de worker ni contenedores temporales de prueba.

Límite de validación: se probaron fixtures de esquema y relaciones, no una
restauración completa de datos reales, carga concurrente ni integración MQTT.

## Procedimiento de aplicación (plan previo; ejecución registrada abajo)

1. Autorizar explícitamente la aplicación; revisar cambios y conservar un respaldo
   consistente y verificable de PostgreSQL. Comprobar de nuevo servidor, base,
   revisión `20260818_01`, definiciones reales, sesiones/bloqueos y worker apagado.
2. Disponer de Alembic y de los archivos de esta revisión en un ejecutor puntual.
   La imagen activa no incorpora automáticamente los archivos nuevos del host.
   No basta ejecutar Alembic dentro de ella sin proporcionar esta revisión.
   Se puede planificar un contenedor puntual con `api/` montado en solo lectura,
   las credenciales privadas del VPS y la red de la base, sin arrancar la API.
   Revisar ese comando antes de ejecutarlo; no reconstruir servicios por este paso.
3. Ejecutar solamente `alembic upgrade 20261104_01` desde `api/` contra la base
   verificada. No ejecutar autogenerate, `create_all`, el worker ni migraciones
   implícitas de arranque. Si hay un timeout, diagnosticar; no quitar límites
   ni reintentar en bucle automáticamente.
4. Comprobar revisión nueva, las cinco definiciones, recuentos de filas y relaciones
   antes/después. En Debian futuro, repetir la inspección y aplicar la misma
   revisión: debería validar columnas y registrar la revisión sin alterar valores.
5. Integrar modelos, esquemas y worker como tarea posterior. Los modelos del VPS
   todavía no incluyen estas columnas; autogenerate podría proponer eliminarlas.
   Mantener el worker apagado y `API_STARTUP_READ_ONLY=true` en la API.

## Aplicación verificada en VPS — 2026-11-04

Finalizada a las **16:46:54 UTC (13:46:54 UTC-03:00)**. Se aplicó exclusivamente
el salto `20260818_01` → `20261104_01` sobre `iot_platform`. No se ejecutó
`stamp`, autogenerate, la migración anterior ni inicialización de la API.

### Respaldo previo y restaurabilidad

Directorio fuera del repositorio, con permisos `0700`:

`/home/hugo/backups/iot-platform/20261104T164535Z-firmware/`

Archivo: `postgresql.dump`, formato custom de `pg_dump`, **1.293.036 bytes**,
permisos `0600`. Incluye esquema, datos, secuencias, propietarios y privilegios
de la base; no incluye contraseñas de roles globales del clúster. No se mostraron
datos del respaldo ni credenciales. El SHA-256 está en `postgresql.dump.sha256`;
se volvió a comprobar al finalizar.

Validaciones realizadas antes de migrar:

- `pg_restore --list` pudo leer el archivo completo de catálogo.
- Restauración real con `pg_restore --exit-on-error --single-transaction` en un
  PostgreSQL descartable de la misma imagen que el activo, sin conexión a la red
  del proyecto, sin puertos publicados y con PGDATA temporal en tmpfs.
- Se crearon únicamente roles de prueba con los nombres necesarios, sin copiar
  contraseñas. La restauración aceptó propietarios y privilegios del archivo;
  no se omitieron con `--no-owner` ni `--no-privileges`.
- Coincidieron los recuentos y huellas de todas las filas de **24 tablas,
  85.365 registros**, y los estados de todas las secuencias, contra una lectura
  consistente previa. La revisión restaurada era `20260818_01`.
- Se eliminó la instancia descartable después de verificar el respaldo. No hubo
  restauración sobre la base activa.

Los manifiestos de comprobación y resultados se conservan en el mismo directorio:
`verification-before.json`, `services-before.json`, `restore-validation.json`,
`migration-result.json`, `migration-alembic.log` y `final-validation.json`.
Son registros privados; no incorporarlos a Git ni publicar el respaldo.

### Bloqueos y ejecutor

Antes del respaldo e inmediatamente antes del upgrade: ningún bloqueo de otra
sesión sobre `public.devices`, ninguna sesión idle in transaction ni sesión
bloqueada en esta base. Se verificó el worker ausente y la revisión de partida.
El ejecutor volvió a comparar los datos con el manifiesto previo antes del DDL.
Los límites de bloqueo de la revisión permanecieron activos para cubrir carreras.

Se utilizó un contenedor puntual de la imagen existente de la API, con `api/`
actualizado montado en solo lectura, raíz de solo lectura y la red privada del
proyecto. Recibió la configuración privada del VPS sin imprimirla. Ejecutó Python
y Alembic, sin Uvicorn ni worker. Se eliminó automáticamente al terminar.
No se reconstruyó ni reinició ningún servicio activo.

### Resultado posterior y pendientes

- Alembic registra `20261104_01`.
- Las cinco columnas tienen los tipos de la tabla inicial, admiten NULL y no
  tienen DEFAULT. Todos los registros anteriores tienen NULL en esos campos.
- Coinciden las huellas de todas las columnas anteriores de las otras 23 tablas,
  incluyendo IDs, usuarios, permisos y relaciones; también las secuencias.
  La única modificación de datos esperada es `alembic_version`.
- Los cinco contenedores de servicio conservan IDs y horas de arranque originales.
  `API_STARTUP_READ_ONLY=true` permanece configurado. No hay contenedor del worker
  ni contenedores temporales restantes. No se accedió a Debian local.
- No se hicieron commits ni push. Se conservaron los cambios previos del proyecto.

Actualización posterior: modelos/esquemas y worker integrados en código y probados;
ver [integración funcional](integracion-funcional-debian-vps.md). Su despliegue y
la activación del worker continúan pendientes de autorización. La migración del
esquema ya está terminada.
El respaldo está validado para restauración en el VPS, pero sigue siendo una
copia en el mismo servidor; no se creó una copia externa en esta tarea.

## Continuidad

No se hicieron commits ni push. Esta nota deja el traspaso técnico dentro del
alcance autorizado (`/srv/iot-platform`). Proponer registrar esta preparación en
`/srv/infra-docs/continuidad/TAREAS.md` y un traspaso que enlace este documento;
revisar `infraestructura/ESTADO-INFRAESTRUCTURA.md` para reflejar esta aplicación.
Esos documentos compartidos no se modificaron en esta tarea.
