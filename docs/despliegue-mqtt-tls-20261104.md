# Despliegue MQTT TLS — detenido antes de exponer el puerto

Nota posterior: este bloqueo describe el intento de las 20:14 UTC. En una revisión
posterior se comprobó TLS activo, con Mosquitto iniciado a las 20:33 UTC. Ver
[estado y verificaciones actuales](verificacion-acl-renovacion-mqtt.md).

Verificado 2026-11-04 a las 20:14 UTC en `vps-aca3e209-vps-ovh-us` por Codex.
El usuario autorizó el despliegue siguiendo
[el procedimiento preparado](mqtt-tls-laboratorio.md).

**Estado: bloqueado por falta de privilegios del sistema; no desplegado.**
La autorización está otorgada, pero esta sesión ejecuta como `hugo` (UID 1000)
y no tiene sudo no interactivo disponible para las operaciones necesarias.

## Validación previa y motivo de la detención

- Se revisaron Git y el procedimiento. HEAD `bb5b568`; se conservaron todos los
  cambios sin commit previos.
- UFW y nft están instalados. `sudo -n -l`, `sudo -n /usr/sbin/ufw status verbose`
  y `sudo -n /usr/sbin/nft list ruleset` no pudieron ejecutarse por falta de
  contraseña/privilegios disponibles. No se pudo verificar la política efectiva
  de firewall ni su interacción con los puertos publicados por Docker.
- La clave privada de Certbot no es accesible al usuario actual. No se leyó,
  copió ni mostró. Por tanto, no se pudo comprobar el par real certificado/clave
  ni provisionar el staging privado con permisos root:1883.
- La verificación TLS del HTTPS existente sí pasó: SAN
  `iot.panozoelectronica.com.ar`, vencimiento 2027-01-14 17:56:52 UTC. Esto no
  reemplaza la validación pendiente del par que utilizará Mosquitto.

Se detuvo el procedimiento antes de provisionar secretos, instalar hooks,
recrear Mosquitto o publicar 8883, conforme a la condición de seguridad del usuario.
No se emplearon mecanismos alternativos para obtener privilegios del host.

## Estado real conservado

Los cinco contenedores siguen running, con sus IDs y horas de arranque anteriores:

| Servicio | ID | Arranque UTC |
| --- | --- | --- |
| API | c55e39730275 | 2026-11-04 17:03:59 |
| Dashboard | 7b2d6ce8d200 | 2026-11-04 17:03:59 |
| Mosquitto | aacd2342a0cc | 2026-11-03 21:33:46 |
| PostgreSQL | 82e510aadcaf | 2026-11-03 21:33:46 |
| Redis | 3c8b560f0c44 | 2026-11-03 21:33:46 |

Mosquitto continúa con el archivo original, no el candidato TLS. Docker publica
1883 y 9001 en `127.0.0.1`; no existe listener host en 8883. Worker ausente.
No se modificaron firewall, DNS, certificados, credenciales, datos, firmware ni
Debian local. No se publicaron eventos/comandos ni se hicieron commit/push.
Los únicos archivos modificados en este intento son esta nota y el enlace de
estado en `mqtt-tls-laboratorio.md`.

## Requisito para retomar

Reanudar desde una sesión con acceso root/sudo disponible o mediante un ejecutor
privilegiado revisado y acotado al procedimiento. No hace falta volver a solicitar
autorización funcional para las acciones que el usuario ya aprobó; falta el acceso
técnico del sistema.

Repetir la validación del estado real y firewall antes de provisionar. Después,
continuar con los auxiliares de credenciales/certificados, validación aislada con
secretos definitivos, instalación root-owned de renovación, recreación exclusiva
de Mosquitto y habilitación restringida de 8883/TCP. Las pruebas externas de TLS,
autenticación y ACL quedan pendientes; no se presentan las pruebas aisladas previas
como validación de un despliegue que todavía no existe.

Para continuidad compartida, se propone registrar este bloqueo y enlazar esta nota
en `/srv/infra-docs/continuidad/TAREAS.md` y su traspaso. La documentación compartida
no fue modificada en esta etapa.
