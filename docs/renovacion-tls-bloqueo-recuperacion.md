# Renovación TLS: bloqueo, verificación y recuperación

Preparado y probado el 2026-11-04 en VPS por Codex. Cambios limitados al auxiliar
del proyecto, sus pruebas y esta documentación. **No instalado como hook ni
ejecutado sobre certificados o servicios activos.**

## Comportamiento

`mosquitto/tools/renew-tls.py --check-only` conserva su funcionamiento: verifica el
certificado, hostname, vigencia, cadena y correspondencia con la clave. No crea
staging ni archivos de bloqueo, no consulta Docker y no envía señales. Las consultas
de solo lectura pueden coincidir sin interferir con una renovación.

Las ejecuciones que modifican staging o recargan adquieren un bloqueo exclusivo
no bloqueante con `flock`, en:

`/srv/iot-platform-secrets/mqtt/tls/.renew.lock`

El archivo es privado (0600), regular, del usuario ejecutor root y no puede ser un
symlink. Si otro proceso mantiene el bloqueo, el segundo termina con error antes
de validar/copiar la versión nueva o señalar el broker. El descriptor se conserva
hasta finalizar, incluida cualquier recuperación. Se libera al cerrar o terminar
el proceso. El archivo no se elimina: reemplazar su inode permitiría dos bloqueos
independientes. No borrar el lock para resolver una contención; revisar el proceso.

## Flujo de `--reload`

1. Bajo el bloqueo, validar la línea Certbot y preparar una versión privada nueva.
   Mantener las guardas de contenedor/configuración/montaje del auxiliar existente.
2. Exigir `current` como symlink válido dentro de `tls/releases/`. Guardar su texto
   exacto, relativo o absoluto, y la huella SHA-256 del certificado anterior.
   Si falta un estado anterior recuperable, no promover ni enviar señales.
3. Obtener la dirección IPv4 local y el puerto 8883 publicados por el ID concreto
   del contenedor. No usar resolución DNS pública para seleccionar el destino.
4. Sustituir `current` atómicamente por la versión nueva y enviar SIGHUP únicamente
   al ID previamente verificado. No hay reinicios ni recreaciones de contenedores.
5. Abrir conexiones TLS nuevas a ese destino local, con SNI y validación del hostname
   `iot.panozoelectronica.com.ar`, cadena confiable del sistema y fechas vigentes.
   Comparar la huella del certificado hoja con la versión preparada. No se envían
   paquetes MQTT, credenciales, publicaciones ni comandos durante esta verificación.
6. Si SIGHUP falla, vence su timeout o no se observa el certificado esperado:
   restaurar atómicamente el texto exacto del symlink anterior, intentar un segundo
   SIGHUP al mismo ID y verificar por TLS la huella del certificado previo.

Aunque la recuperación funcione, la ejecución devuelve error (código 1), con un
mensaje explícito de recuperación comprobada: la renovación nueva no se completó
y debe ser atendida. Si no se puede restaurar el enlace o comprobar el certificado
previo, devuelve error que exige intervención manual. No intenta recrear servicios,
rotar credenciales, borrar versiones, restaurar bases ni resolver otros problemas.
Los archivos de versiones anteriores y de la candidata se conservan.

## Plazos

```bash
# Ejemplo para una ventana futura autorizada; NO ejecutado en esta tarea.
python3 -B mosquitto/tools/renew-tls.py --reload --verify-timeout 20
```

`--verify-timeout` admite entre 1 y 120 segundos; predeterminado 20. Es el plazo
total de comprobación TLS de cada fase: hasta 20 s para la nueva y, si falla, hasta
otros 20 s para recuperar la anterior. Cada conexión/handshake tiene además límite
de hasta 3 s o el tiempo restante, lo que sea menor. Se usa reloj monotónico.
`docker inspect` y cada señal SIGHUP tienen límites de 10 s; no se reintentan señales
indefinidamente. Los tiempos de Docker se suman a las ventanas de verificación.

La comprobación usa el puerto **8883** publicado localmente en IPv4. Si cambia el
despliegue a otro puerto o solo IPv6, el auxiliar rechaza esa configuración antes
de sustituir `current`; requiere una adaptación revisada, no una búsqueda de otros
destinos. Los sockets de las pruebas usan puertos locales efímeros independientes.

## Pruebas ejecutadas

```bash
python3 -B -m unittest discover -s mosquitto/tests -p test_renew_recovery.py -v
python3 -B -m unittest discover -s mosquitto/tests -p test_certificate.py -v
```

Resultado: **18 pruebas nuevas y 9 de regresión aprobadas**.

- Exclusión real entre procesos con flock, liberación tras excepciones, archivo
  privado y rechazo de permisos inseguros.
- Promoción exitosa, fallo de señal, timeout por certificado inesperado, restauración
  del enlace relativo/absoluto y segunda recarga del certificado previo.
- Fallos durante restauración o verificación de recuperación: error explícito,
  sin ocultar el incidente ni intentar reiniciar contenedores.
- Ausencia de `current` anterior, modo staging sin señales y `--check-only` sin
  bloqueo/staging. Argumentos de timeout fuera de rango rechazados antes de operar.
- Conexiones TLS reales a un servidor temporal en loopback: certificado esperado,
  CA no confiable y huella incorrecta con deadline acotado.
- Coordinador completo con dos certificados de laboratorio y TLS real: éxito al
  servir el nuevo y recuperación comprobada cuando el servidor sigue sirviendo el
  anterior. La señal de control se simula; no se llamó a Docker para señalar el
  broker activo.
- Regresiones de validación de certificados y provisión: continúan pasando.

Los certificados, claves y symlinks usados por las pruebas fueron temporales.
La CA de prueba incluye las extensiones necesarias para validación estricta de
Python/OpenSSL; no se desactivó verificación para hacer pasar los tests.
No se ejecutó la renovación real ni se modificó la copia root-owned instalada,
si existiera. `--check-only` real no se repitió: requiere acceso a la clave privada
y el usuario ya lo había comprobado correctamente.

## Pendiente operativo y límites

Revisar los cambios y coordinar una ventana autorizada antes de utilizar el modo
mutante o actualizar cualquier copia root-owned del auxiliar. **No instalar todavía
el hook de Certbot**, conforme a la instrucción vigente. Para el futuro hook, un
retorno no cero sigue requiriendo alerta incluso si el certificado anterior quedó
recuperado.

El bloqueo coordina instancias de este auxiliar; no impide cambios manuales de otro
proceso root que no lo respete. La recuperación descrita cubre errores capturados
de recarga/verificación, no es un journal persistente ante SIGKILL o apagado brusco.
No eliminar ni editar manualmente `current` o sus versiones durante la ejecución.

No se activó el worker, no hubo reinicios ni cambios de certificados activos,
firmware, cuentas, ACL o datos. Sin commit ni push.
