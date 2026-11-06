# Verificación de ACL y renovación MQTT TLS

Actualización del auxiliar: ver [bloqueo, verificación TLS y recuperación automática
del enlace anterior](renovacion-tls-bloqueo-recuperacion.md). Está probado en
aislamiento, sin instalar hooks ni modificar certificados activos.

Verificado 2026-11-04 en VPS `vps-aca3e209-vps-ovh-us` por Codex.

## Estado actual comprobado

- Mosquitto activo: contenedor `91a976a0aa20`, iniciado a las 20:33:18 UTC.
  Monta la configuración TLS y los directorios privados de autenticación/TLS.
  8883 se publica en `40.160.233.73`; 1883 y 9001 conservan loopback.
- Handshake público de `iot.panozoelectronica.com.ar:8883` correcto: TLS 1.3,
  hostname y cadena verificados, vencimiento 2027-01-14 17:56:52 UTC.
- Los otros cuatro servicios mantienen sus IDs/horas de arranque. Worker ausente.
- Historial informado por el usuario: autenticación válida/inválida y escritura
  ajena rechazada con PUBACK 135 en producción. No se repitieron esas publicaciones
  contra el broker activo en esta tarea.
- La lectura en producción sigue pendiente de una prueba controlada. Los éxitos
  del entorno descartable descritos abajo no se presentan como pruebas activas.

## Correcciones y archivos

- `mosquitto/tools/provision-public-auth.py`: argparse procesa `--help` antes de
  privilegios, Docker o escrituras. Se mantiene el rechazo a sobrescribir cuentas.
  `hash_passwordfile()` conserva la raíz Docker read-only y añade `/tmp` tmpfs
  escribible, necesario para el archivo temporal de `mosquitto_passwd`.
  No se generó ni rotó ninguna contraseña real en esta tarea.
- `mosquitto/tools/mqtt_probe_client.py`: cliente síncrono compartido, sin dependencias
  externas; soporta MQTT 5, TLS validado, QoS 1 y recepción de PUBLISH intercalados
  antes del PUBACK. No contiene credenciales.
- `mosquitto/tools/check-mqtt-acl.py`: prueba reutilizable de escritura/lectura
  permitidas y denegadas, con tópicos exclusivamente ficticios y controles de entrega.
- `mosquitto/tools/certbot-mqtt-deploy.sh`: plantilla de hook; no instalada.
- `mosquitto/tests/test_certificate.py`, `test_acl_probe.py`, `test_tls.py` y
  `docs/test-mqtt-tls.py`: regresiones, guardas y ejecución completamente aislada.

## Prueba automatizada de ACL

Sin opciones, el auxiliar solo presenta el plan: **no lee credenciales ni abre
conexiones**. `--help` tampoco realiza acciones:

```bash
python3 -B mosquitto/tools/check-mqtt-acl.py
python3 -B mosquitto/tools/check-mqtt-acl.py --help
```

Con `--run` se ejecuta en el VPS: dispositivo por TLS al dominio público y observador
por 1883 loopback. Usa MQTT 5, sin retain, sin LWT y con sesiones limpias. No envía
comandos ni eventos de aplicación: solo marcadores efímeros bajo
`iot/devices/acl-probe/<identificador-aleatorio>/`:

| Sufijo | Acción esperada del dispositivo |
| --- | --- |
| write-ok | Publicar permitido, PUBACK 0 y entrega observada |
| write-denied | Publicar denegado, PUBACK 135 y sin entrega |
| read-ok | Recibir publicación sintética del observador |
| read-denied | No recibir publicación sintética del observador |

La prueba rechaza namespaces de dispositivos reales antes de conectar. Un SUBACK
exitoso no significa permiso de lectura: se publican estímulos sintéticos desde el
observador y un marcador permitido posterior demuestra que la entrega sigue viva.
También se comprueba que no aparezcan mensajes inesperados después del marcador.
Un timeout aislado o un SUBACK no se consideran prueba suficiente.

### Credenciales privadas

El perfil por defecto es:
`/srv/iot-platform-secrets/mqtt/acl-probe-credentials.json`.
Debe contener objetos `device` y `observer`, cada uno con `username` y `password`,
de identidades distintas. Puede reutilizar cuentas existentes; no necesita rotarlas.
El dispositivo usa la cuenta de laboratorio y el observador una cuenta interna
autorizada a leer/publicar en el namespace ficticio.

El lector exige archivo regular con permisos 0600 o más restrictivos, propietario
root/usuario ejecutor y ruta resuelta dentro del directorio privado. El JSON no
se imprime; las contraseñas no se pasan por argv ni por variables de entorno.
Errores solo informan su tipo, sin cuerpo de excepción, paquetes ni trazas sensibles.

La sesión actual `hugo` no puede leer ese directorio y no dispone de sudo sin
contraseña. No se intentó sortear esos permisos ni se solicitó copiar secretos al
repositorio. No se creó el perfil definitivo.

### Modificación que requiere autorización antes de probar en producción

La ACL actual solo permite los tópicos propios del equipo de laboratorio. No permite
publicar/leer los tópicos ficticios anteriores. **No se editó la ACL activa.**
Para habilitar temporalmente esta prueba se requiere autorización específica para
agregar, bajo la cuenta de laboratorio, únicamente:

```text
topic write iot/devices/acl-probe/+/write-ok
topic read iot/devices/acl-probe/+/read-ok
```

El observador necesita acceso al namespace completo; la cuenta interna ya usada
en los tests tiene ese acceso bajo su ACL existente. No otorgar acceso a
`write-denied`/`read-denied` al dispositivo. Preparar el perfil privadamente con
acceso root y coordinar la aplicación/recarga de esa ACL antes de ejecutar:

```bash
python3 -B mosquitto/tools/check-mqtt-acl.py --run
```

Después, retirar solo las dos autorizaciones temporales mediante una operación
aprobada y verificar la política original. No borrar históricos ni cambiar otras
cuentas/reglas. La herramienta no modifica ACL, credenciales ni firewall por sí misma.

## Pruebas ejecutadas sin cambios de producción

```bash
python3 -B -m unittest discover -s mosquitto/tests -p test_certificate.py -v
python3 -B -m unittest discover -s mosquitto/tests -p test_acl_probe.py -v
python3 -B docs/test-mqtt-tls.py
```

- **9 pruebas auxiliares OK**, incluida generación real de hash en Docker con raíz
  read-only y `/tmp` tmpfs; ninguna contraseña en argumentos. `--help` comprobado
  sin llamadas a Docker, escrituras o consulta de privilegios.
- **5 pruebas de seguridad del nuevo auxiliar OK**: ayuda, plan sin acceso a secretos,
  rechazo de namespace real, permisos privados y errores sin datos sensibles.
- **14 escenarios MQTT/TLS OK**, repetidos después de renovar y recargar únicamente
  el certificado del broker descartable. Aislamiento de cuentas, TLS, WebSocket y
  ACL originales mantienen sus resultados.
- El auxiliar nuevo se ejecutó realmente en ese broker con un perfil privado
  temporal y las dos reglas sintéticas añadidas **solo a la copia descartable**:
  escritura permitida RC 0 y entrega; escritura denegada RC 135 sin entrega;
  lectura permitida entregada; lectura denegada sin entrega aunque SUBACK fue 1.
- Todos los clientes de MQTT estuvieron en un namespace Docker sin red externa;
  ningún puerto host publicado ni conexión MQTT al broker activo. Se usó un
  certificado/CA de pruebas, nunca la clave Certbot real.
- Se eliminaron fixtures y contenedores; IDs, arranques y reinicios de servicios
  activos no cambiaron. No se accedió a bases de datos ni se modificó firmware.

## Renovación: hallazgo y procedimiento pendiente

Certbot tiene timer activo y su último servicio terminó con éxito. Sin embargo,
no se encontró hook de Mosquitto en `renewal-hooks/{deploy,post,pre}`, ni en la
configuración de renovación del dominio, `cli.ini` o argumentos del servicio
estándar Certbot. Tampoco se encontraron auxiliares MQTT instalados en
`/usr/local/{libexec,bin,sbin}`. Por tanto, **no se considera verificada una recarga
automática de la copia TLS de Mosquitto** cuando renueve Certbot.

`renew-tls.py` del proyecto valida nombre, fechas, cadena y par, publica una versión
privada mediante symlink atómico y dirige SIGHUP al ID de un broker TLS comprobado.
Su guarda de destino identifica correctamente el contenedor actual en una consulta
de solo lectura; no se ejecutó `--reload` contra producción.

Preparación revisable para la operación root pendiente:

1. Instalar una copia root-owned revisada de `renew-tls.py` en
   `/usr/local/libexec/iot-mqtt-renew-tls.py`.
2. Instalar `certbot-mqtt-deploy.sh`, root-owned, en un archivo de
   `/etc/letsencrypt/renewal-hooks/deploy/`. La plantilla actúa solo para la línea
   `iot.panozoelectronica.com.ar`; otras líneas no realizan acciones.
3. Ejecutar primero `--check-only` con root. Coordinar una prueba de renovación/
   recarga dirigida; no ejecutar automáticamente un dry-run de Certbot, ya que
   sus plugins/hooks podrían afectar Nginx u otros servicios.
4. Tras la recarga autorizada, comprobar mediante conexión TLS nueva el certificado
   servido, cadena/vencimiento y autenticación. Nunca registrar la clave privada.

No se instalaron hooks ni se recargó Mosquitto. La provisión real de credenciales,
sus ACL y el certificado activo permanecen intactos. Sin commit ni push.
