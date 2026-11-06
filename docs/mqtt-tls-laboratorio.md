# MQTT TLS de laboratorio — preparación e historial

Estado vigente verificado el 2026-11-04: TLS está operativo en 8883. La revisión
actual, las pruebas ACL y los pendientes de renovación están en
[verificacion-acl-renovacion-mqtt.md](verificacion-acl-renovacion-mqtt.md).
Las secciones siguientes conservan la preparación y el intento anterior como historial.

Actualización 2026-11-04: el despliegue fue autorizado, pero se detuvo antes de
exponer 8883 por falta de privilegios root/sudo. Ver
[el registro del intento y requisitos para retomar](despliegue-mqtt-tls-20261104.md).

Verificado 2026-11-04 en VPS `vps-aca3e209-vps-ovh-us` por Codex.
Objetivo: `iot.panozoelectronica.com.ar:8883`, TLS mínimo 1.2, autenticación pública
independiente. El broker activo y su configuración montada no se modificaron.
No se abrieron puertos, no hubo recargas/reinicios activos ni conexiones MQTT al
broker activo. Worker ausente; Arduino/ESP8266, firewall y DNS intactos.

## Archivos y diseño

| Archivo | Uso |
| --- | --- |
| `mosquitto/config/mosquitto-tls.conf` | Configuración candidata completa, separada del `mosquitto.conf` activo. |
| `mosquitto/config/acl-public` | ACL exclusiva del equipo de laboratorio. |
| `docker-compose.mqtt-tls.yml` | Override opt-in: configura solo Mosquitto, conserva puertos loopback y añade 8883 sobre la IPv4 pública del VPS. |
| `mosquitto/tools/provision-public-auth.py` | Provisión futura de contraseña fuera de Git, sin reemplazar credenciales internas. No ejecutado contra rutas definitivas. |
| `mosquitto/tools/renew-tls.py` | Validación y staging de certificados; recarga dirigida solo si este despliegue TLS ya está activo. No instalado ni ejecutado sobre Certbot real. |
| `mosquitto/tests/test_tls.py`, `test_certificate.py` | Pruebas de protocolo, aislamiento, certificados y auxiliares. |
| `docs/test-mqtt-tls.py` | Ejecutor reproducible con broker descartable. |
| `.gitignore` | Exclusiones defensivas para contraseñas y claves privadas. |

El override fija la misma imagen ya comprobada en VPS:
`eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408`
(Mosquitto 2.1.2). No se usó una actualización implícita de imagen para probar.

El Compose predeterminado sigue sin publicar 8883. Solo al seleccionar expresamente
ambos archivos se proyectan estos puertos:

| Host | Contenedor | Autenticación |
| --- | --- | --- |
| 127.0.0.1:1883 | 1883 MQTT | Archivo y ACL internos actuales |
| 127.0.0.1:9001 | 9001 WebSocket | Archivo y ACL internos actuales |
| 40.160.233.73:8883 | 8883 MQTT TLS | Archivo público nuevo y ACL mínima |

1883 permanece accesible internamente como `mosquitto:1883`; no debe ligarse al
loopback dentro del contenedor, porque impediría conectar a los servicios Docker.
El override no cambia redes, volúmenes de datos, worker ni otros servicios.
Los bind mounts nuevos son read-only y usan `create_host_path: false`: faltantes
de secretos impiden iniciar en lugar de crear directorios vacíos silenciosamente.

## Autenticación e identidad

Se usan instancias `plugin_load`/`plugin_use` de los plugins incluidos de
contraseñas y ACL, asignadas explícitamente a cada listener. No hay autenticación
global heredada ni `per_listener_settings` obsoleto en el candidato.
Referencia: [separación por listener en Mosquitto 2.1](https://mosquitto.org/documentation/listeners/per-listener-settings/).

Identidad preparada:

- Device ID/tópicos: `lab-esp8266-dell-20261104-01`.
- Usuario MQTT: `lab-esp8266-dell-01`.
- Contraseña definitiva: pendiente de generar privadamente con el auxiliar de
  provisión. Los secretos de prueba se eliminaron; no son credenciales desplegables.
- En 8883, `use_username_as_clientid true` fuerza el client ID efectivo al usuario
  autenticado. El ESP puede solicitar su DEVICE_ID, pero el broker lo reemplaza.
  Esto evita que una cuenta pública tome el client ID de un cliente interno y lo
  desconecte. Solo debe existir un equipo por usuario público.

La ACL permite escribir exclusivamente `event`, `info`, `status` y `telemetry`
del dispositivo; leer exclusivamente su `cmd` y `iot/platform/worker/heartbeat`.
No permite publicar comandos/heartbeat ni leer otros dispositivos. Permite el LWT
en su propio tópico `status`. Las cuentas internas no están en el archivo público;
la cuenta pública tampoco se incorpora al archivo interno.

No se dio de alta ningún registro en PostgreSQL. El aprovisionamiento MQTT y los
permisos de usuarios web son independientes; la ingesta futura requiere el worker.

## Certificados y secretos fuera del repositorio

El dominio ya resuelve a la IPv4 del VPS. En el diagnóstico previo se verificó
el certificado servido por HTTPS: SAN `iot.panozoelectronica.com.ar`, vigente hasta
2027-01-14, cadena confiable. La configuración candidata apunta a una copia
controlada de esa misma línea Certbot, no al certificado efímero de pruebas.

**Pendiente antes de desplegar:** leer y validar con root el par real de Certbot,
copiarlo al staging privado y comprobar los permisos efectivos de Mosquitto
(UID/GID 1883 en la imagen verificada). No se extrajo la clave privada actual ni
se incorporó a Git. No se certifica todavía un endpoint TLS real en 8883.

Rutas definitivas propuestas, aún no provisionadas por esta tarea:

```text
/srv/iot-platform-secrets/mqtt/
  lab-device-credential.json       # root:root 0600; fuera de los mounts del broker
  auth/                           # root:1883 0750
    passwordfile                  # root:1883 0640; hash de la cuenta pública
  tls/                            # root:1883 0750; montaje de directorio completo
    current -> releases/<versión>
    releases/<versión>/            # root:1883 0750
      fullchain.pem               # root:1883 0640
      privkey.pem                 # root:1883 0640
```

El auxiliar de provisión se niega a sobrescribir una credencial existente,
genera una contraseña aleatoria y la pasa a `mosquitto_passwd -U` mediante un
archivo privado nuevo, nunca por argumentos de proceso. La conversión ocurre en
un contenedor puntual sin red; no toca el passwordfile interno ni el broker activo.
Si falla, se detiene y conserva archivos privados para revisión; no reintentar
sobrescribiendo ni publicar su contenido.

## Pruebas ejecutadas y límites

```bash
cd /srv/iot-platform
python3 -B docs/test-mqtt-tls.py
python3 -B -m unittest discover -s mosquitto/tests -p test_certificate.py -v
```

Resultado: **14 casos MQTT/TLS aprobados antes y después de recargar un certificado
de prueba**, más **7 pruebas de auxiliares aprobadas**.

- `mosquitto --test-config` y arranque del candidato en un contenedor con red
  `none`, sin puertos publicados, datos en tmpfs y fixtures privados temporales.
- Copia efímera del passwordfile interno real y su ACL: la credencial interna
  configurada en el VPS autentica en 1883 y WebSocket, y es rechazada en TLS.
  El archivo público contiene únicamente la cuenta de laboratorio y no comparte
  usuarios con el interno. Ningún hash, contraseña o CONNECT se imprimió.
- Cuenta pública aceptada solo en TLS, anónimos rechazados y contraseña incorrecta
  rechazada. Se comprobó también MQTT 3.1.1, utilizado por el ESP8266, además de MQTT 5.
- Publicaciones y lecturas permitidas comprobadas con un observador interno;
  comandos, heartbeat y tópicos ajenos denegados. Solo se publicaron payloads
  sintéticos en el broker descartable, nunca en el activo.
- TLS 1.2/1.3 aceptados; TLS 1.1 rechazado con alerta de protocolo. CA desconocida
  y hostname incorrecto rechazados por el cliente. Reconexiones autenticadas.
- Un cliente público que solicita el ID de un cliente interno no lo desconecta.
- Se renovó un certificado efímero y se envió SIGHUP únicamente al contenedor de
  prueba; una conexión nueva comprobó la huella nueva. Se repitió toda la suite
  para verificar que la recarga no rompe la separación de credenciales/ACL.
- Auxiliares: cadena/hostname/fechas/par correctos, clave incorrecta, CA desconocida,
  hostname incorrecto, expiración simulada, rechazo de recarga en despliegue sin TLS,
  provisión privada sin secretos en argv/salida ni sobrescritura.
- IDs, horas de arranque, contadores de reinicio y estado de los cinco servicios
  activos coinciden antes/después. Worker ausente. Fixtures y contenedores de
  prueba eliminados; puerto host 8883 sigue sin listener.

Los certificados de prueba usan una CA temporal local y SAN del dominio real.
Esto valida la configuración TLS, no sustituye la validación del certificado y
clave Certbot reales ni mide memoria/compatibilidad BearSSL en el ESP8266.
No se modificó firmware ni se ejecutaron pruebas end-to-end de ingesta o dispositivos.

## Procedimiento futuro de despliegue — NO ejecutado

1. Obtener autorización de despliegue. Revisar estado real, versión/digest, respaldos
   de persistencia/configuración y reglas de firewall del host/OVH. No asumir que
   una regla INPUT protege un puerto publicado mediante Docker/DNAT. No publicar
   IPv6 sin revisar su política; el override propone solo la IPv4 indicada.
   Aplicar primero la política de acceso aprobada para 8883, antes de publicar
   el puerto con Docker; la publicación puede instalar reglas de aceptación.
2. Revisar/ejecutar como root `mosquitto/tools/provision-public-auth.py`. Custodiar
   el registro privado fuera de Git; no copiarlo a tickets, logs ni comandos.
   Configurar la cuenta en el firmware será una tarea autorizada aparte.
3. Revisar/ejecutar como root `mosquitto/tools/renew-tls.py --check-only` y luego
   sin opciones para preparar el certificado real. Sin `--reload`, no señala ningún
   contenedor. El auxiliar valida nombre, fechas, al menos 24 h de vigencia,
   cadena del sistema y coincidencia certificado/clave antes del staging atómico.
4. Validar el candidato con los secretos definitivos en un contenedor puntual
   `--network none`, sin puertos, con config/auth/TLS en read-only y datos tmpfs.
   Usar `mosquitto -c /mosquitto/config/mosquitto-tls.conf --test-config`; no montar
   el directorio de datos activo. Si falla la separación o validación, detenerse.
5. Revisar el Compose combinado sin imprimir su contenido completo (expande secretos):

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.mqtt-tls.yml config --quiet
   ```

6. En la ventana autorizada, recrear únicamente Mosquitto, sin dependencias,
   usando ambos archivos. El cambio inicial de publicación/montajes/listener
   requiere recreación; SIGHUP no agrega el puerto Docker:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.mqtt-tls.yml \
     up -d --no-deps --no-build --force-recreate mosquitto
   ```

7. Comprobar la exposición 8883/TCP bajo las políticas previamente aprobadas.
   Verificar desde fuera TLS, nombre, cadena, autenticación y ACL negativas;
   confirmar que 1883/9001 siguen restringidos al host y que los clientes internos
   conservan acceso. No activar el worker ni enviar comandos a dispositivos.
8. La base Compose sola continúa representando el despliegue sin TLS: después de
   activar el override, usar siempre ambos archivos para operaciones de Mosquitto;
   omitirlo podría retirar el listener público y sus montajes.

Los parámetros opcionales `IOT_MQTT_TLS_BIND_IP`, `IOT_MQTT_PUBLIC_AUTH_DIR` y
`IOT_MQTT_TLS_DIR` están en el override. Los auxiliares están preparados para las
rutas fijas anteriores: cambiar rutas requiere revisar también los auxiliares,
permisos y guardas antes de ejecutarlos.

## Renovación dirigida — NO instalada

Certbot tiene temporizador activo; no había deploy-hook de Mosquitto. Instalar
una copia revisada y root-owned del auxiliar, por ejemplo en
`/usr/local/libexec/iot-mqtt-renew-tls.py`, en vez de ejecutar desde un repositorio
editable por usuario en una automatización root.

Después del despliegue inicial, configurar un deploy-hook root-owned que invoque:

```bash
/usr/bin/python3 /usr/local/libexec/iot-mqtt-renew-tls.py --reload
```

El auxiliar ignora otras líneas de certificados, valida el par renovado, crea
una nueva versión privada y cambia `current` atómicamente. El montaje de directorio
permite ver esa sustitución sin recrear el broker. Con `--reload` comprueba que el
contenedor realmente publique 8883 y monte este TLS/config candidato, y envía
SIGHUP a su ID exacto. Se niega a señalar el broker actual sin TLS. No reinicia
servicios ni señala Nginx/API. Mosquitto puede recargar también otras opciones
recargables al recibir esa señal; no editar configuraciones ajenas junto al hook.

Actualización posterior: el auxiliar incorpora bloqueo y verificación TLS después
de SIGHUP; si falla, restaura únicamente el enlace anterior e intenta recargar y
comprobar el certificado previo. Ver [procedimiento y pruebas](renovacion-tls-bloqueo-recuperacion.md).
La autenticación se verifica aparte. No borrar versiones ni restaurar datos o
servicios automáticamente. Prever retención y custodia de claves anteriores.

Referencias: [plugins por listener](https://mosquitto.org/documentation/listeners/per-listener-settings/),
[configuración TLS y recarga](https://mosquitto.org/man/mosquitto-conf-5.html).

## Continuidad

Preparación terminada, sin commit ni push y sin despliegue. Proponer registrar el
estado y el enlace a esta nota en el índice/traspaso de `/srv/infra-docs/continuidad`;
actualizar la instantánea de infraestructura cuando se despliegue realmente.
No se editaron documentos compartidos ni los cambios previos de otros componentes.
