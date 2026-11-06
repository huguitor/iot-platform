# Transferencia privada MQTT a la Dell

Herramienta: `mosquitto/tools/transfer-lab-credentials.py`. Preparada y probada
con datos ficticios; **no se ejecutó la transferencia real**.

Ejecutar manualmente como root en el VPS. La conexión SSH se ejecuta como hugo,
usando su alias `arduino-notebook`, configuración y claves habituales. Exige host
key ya conocida y acceso no interactivo; no acepta automáticamente claves nuevas.

```bash
sudo python3 -B /srv/iot-platform/mosquitto/tools/transfer-lab-credentials.py --check-only
sudo python3 -B /srv/iot-platform/mosquitto/tools/transfer-lab-credentials.py
```

El primer comando lee y valida la credencial real y la envía cifrada por SSH para
validar el destino, pero no escribe ni cambia permisos. El segundo actualiza.
No se requiere un argumento con contraseña ni habilitar SSH de root.

Origen fijo: `/srv/iot-platform-secrets/mqtt/lab-device-credential.json`.
Debe ser archivo regular, propietario root y sin permisos para grupo u otros;
se lee sin alterar permisos. Se rechazan campos duplicados, adicionales o
faltantes; se requieren `username`, `device_id`, `password` y la identidad exacta
del laboratorio. La contraseña debe ser ASCII imprimible de 1 a 256 caracteres.

Destino fijo: `/home/hugo/Arduino/esp8266_gateway_wiegand_mqtt/config.h`.
Se exige la rama `feature/mqtt-tls-lab-1.6.0`, archivo ignorado y no seguido por
Git, propietario hugo, sin symlink ni hardlinks. El directorio debe pertenecer a
hugo y no permitir escritura a grupo u otros. Si falla una validación, investigar
manualmente; el script no corrige permisos del directorio ni políticas de acceso.

Solo cambia los inicializadores de DEVICE_ID, MQTT_HOST, MQTT_PORT, MQTT_USER y
MQTT_PASS, fijando dominio y puerto 8883. Conserva Wi-Fi, AP, comentarios y finales
de línea. Rechaza declaraciones ambiguas, condicionales o formato no soportado.
Escapa las cadenas para C++; no ejecuta ni imprime el contenido de config.h.

La credencial permanece en memoria en VPS y viaja por stdin de SSH. No se crean
temporales con secretos en VPS. En Dell se escribe un temporal 600 en el mismo
directorio, se sincroniza y se reemplaza atómicamente el destino; queda propietario
hugo. Si falla antes del reemplazo se elimina el temporal y conserva el original.
No se crean respaldos de credenciales. No ejecutar con trazas que registren stdin.

La salida y errores de SSH se capturan sin mostrarlos; los errores se informan
con texto fijo, sin valores ni detalles de excepciones. Si hay timeout o pérdida
de SSH podría haberse completado el reemplazo: verificar con --check-only antes
de decidir otro intento. Ese modo valida estructura, no autentica contra broker.

No compila ni carga firmware, no publica MQTT, no modifica ACL o servicios y no
activa worker. Una compilación posterior con credenciales reales debe proteger
también temporales y binarios porque incorporan la contraseña.

Pruebas locales sin conexiones SSH ni lectura de credenciales reales:

```bash
cd /srv/iot-platform
python3 -B -m unittest discover -s mosquitto/tests -p test_transfer_lab_credentials.py -v
```

20 pruebas aprobadas: origen privado intacto, bloqueo sin root, estructura/duplicados,
escape C++, preservación de contenido
y CRLF, rechazo de formatos ambiguos, reemplazo atómico y permisos, check-only,
symlinks/hardlinks, recuperación ante fallo de reemplazo, ayuda sin acciones,
valores solo por stdin, ocultamiento de errores y ejecución local del programa
remoto generado contra un repositorio temporal con datos ficticios.

## Diagnóstico verificado el 2026-11-04

El comando sin sudo, ejecutado como hugo, falla antes de leer el origen: requiere
root. Ahora informa `E_ROOT` explícitamente. SSH como hugo funciona y la Dell
está en la rama requerida; config.h está ignorado, es regular, pertenece a hugo
y tiene permisos 600. Su estructura pasó la validación con una identidad ficticia,
sin escribir ningún dato. El directorio del proyecto tiene permisos 775, por lo
que el chequeo remoto falla con `E_DEST_DIR`: permite escritura al grupo.
No se alteraron esos permisos ni se relajó la validación de seguridad.

La lectura y estructura del JSON real no pudieron comprobarse en esta sesión:
hugo no puede acceder al origen y sudo exige contraseña. Se debe repetir la
verificación manual como root después de revisar la política de permisos del
directorio de la Dell. No se ejecutó transferencia real.

Códigos seguros principales:

| Código | Etapa / motivo |
|---|---|
| E_ROOT | Falta ejecutar como root |
| E_SOURCE_ACCESS | No se puede abrir el JSON privado |
| E_SOURCE_MODE | Propietario/tipo/permisos del origen no admitidos |
| E_JSON / E_FIELDS | JSON inválido o estructura no admitida |
| E_IDENTITY / E_PASSWORD / E_ENCODING | Identidad o formato no admitidos |
| E_SSH / E_TIMEOUT | Conexión/protocolo SSH o tiempo de espera |
| E_REMOTE_USER / E_BRANCH / E_GIT_TRACKING | Usuario, rama o exclusión Git |
| E_CONFIG_CONDITIONAL / E_CONFIG_AMBIGUOUS / E_CONFIG_DECLARATION | Estructura de config.h |
| E_DEST_ACCESS / E_DEST_FILE / E_DEST_DIR | Acceso o seguridad del destino |
| E_INTERNAL / E_REMOTE | Fallo no clasificado, detalles omitidos |

Solo se muestran códigos y mensajes constantes de una lista cerrada. Los tokens
remotos deben coincidir exactamente; stdout/stderr inesperados y excepciones se
descartan. Las pruebas adicionales cubren errores permitidos, salida remota
maliciosa, timeout, acceso al origen y directorio con escritura al grupo.
