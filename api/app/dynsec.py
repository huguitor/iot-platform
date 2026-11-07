# api/app/dynsec.py
"""Cliente mínimo del plugin Dynamic Security de Mosquitto.

La API administra los usuarios MQTT de los equipos de fábrica publicando
comandos en `$CONTROL/dynamic-security/v1` con una cuenta propia del
passwordfile interno (listener 1883, red Docker interna). Ver
docs/plan-alta-automatica-equipos.md §3.3 (resultado de la etapa 1).

Una conexión por operación: el alta y la baja de equipos son poco frecuentes.
Nunca se registran contraseñas en logs ni en excepciones.
"""

import json
import os
import threading
import uuid
from typing import List, Optional

CONTROL_TOPIC = "$CONTROL/dynamic-security/v1"
RESPONSE_TOPIC = CONTROL_TOPIC + "/response"

HOST = os.getenv("MQTT_DYNSEC_HOST", "mosquitto")
PORT = int(os.getenv("MQTT_DYNSEC_PORT", "1883"))
USERNAME = os.getenv("MQTT_DYNSEC_USERNAME", "").strip()
PASSWORD = os.getenv("MQTT_DYNSEC_PASSWORD", "")
DEVICE_ROLE = os.getenv("MQTT_DYNSEC_DEVICE_ROLE", "device").strip() or "device"
TIMEOUT = float(os.getenv("MQTT_DYNSEC_TIMEOUT_SECONDS", "5"))

# Un rol para todos los equipos: cada uno solo ve iot/devices/<su usuario>/#.
DEVICE_ROLE_ACLS = [
    {"acltype": t, "topic": "iot/devices/%u/#", "allow": True, "priority": 5}
    for t in ("publishClientSend", "publishClientReceive", "subscribePattern", "unsubscribePattern")
]


class DynsecError(Exception):
    """El broker respondió con error o no respondió."""


class DynsecNotConfigured(DynsecError):
    pass


class DynsecConflict(DynsecError):
    """El usuario o el rol ya existe."""


class DynsecNotFound(DynsecError):
    pass


def configured() -> bool:
    return bool(USERNAME and PASSWORD)


def _run(commands: List[dict]) -> List[dict]:
    """Manda los comandos y devuelve las respuestas en el mismo orden."""
    if not configured():
        raise DynsecNotConfigured("Dynamic Security no está configurado en la API")

    import paho.mqtt.client as mqtt  # import tardío: la API arranca aunque falte

    correlation = uuid.uuid4().hex
    for i, cmd in enumerate(commands):
        cmd["correlationData"] = f"{correlation}-{i}"

    done = threading.Event()
    state = {"responses": {}, "error": None}

    def on_connect(client, _userdata, _flags, rc):
        if rc != 0:
            state["error"] = f"conexión rechazada (rc={rc})"
            done.set()
            return
        client.subscribe(RESPONSE_TOPIC, qos=1)

    def on_subscribe(client, _userdata, _mid, _granted):
        client.publish(CONTROL_TOPIC, json.dumps({"commands": commands}), qos=1)

    def on_message(_client, _userdata, msg):
        try:
            body = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        for resp in body.get("responses") or []:
            cd = str(resp.get("correlationData") or "")
            if cd.startswith(correlation + "-"):
                state["responses"][int(cd.rsplit("-", 1)[1])] = resp
        if len(state["responses"]) == len(commands):
            done.set()

    client = mqtt.Client(client_id=f"api-dynsec-{correlation[:12]}", clean_session=True)
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(HOST, PORT, keepalive=15)
    except Exception as exc:  # red, DNS, puerto
        raise DynsecError(f"no se pudo conectar al broker: {type(exc).__name__}") from None
    client.loop_start()
    try:
        if not done.wait(TIMEOUT):
            raise DynsecError("el broker no respondió a tiempo")
        if state["error"]:
            raise DynsecError(state["error"])
    finally:
        client.disconnect()
        client.loop_stop()
    return [state["responses"][i] for i in range(len(commands))]


def _one(command: dict) -> dict:
    resp = _run([command])[0]
    error = resp.get("error")
    if error:
        low = str(error).lower()
        if "already exists" in low:
            raise DynsecConflict(error)
        if "not found" in low:
            raise DynsecNotFound(error)
        raise DynsecError(error)
    return resp


def ensure_device_role() -> None:
    try:
        _one({"command": "getRole", "rolename": DEVICE_ROLE})
        return
    except DynsecNotFound:
        pass
    try:
        _one({"command": "createRole", "rolename": DEVICE_ROLE, "acls": DEVICE_ROLE_ACLS})
    except DynsecConflict:
        pass  # lo creó otro pedido al mismo tiempo


def get_client(username: str) -> Optional[dict]:
    try:
        return _one({"command": "getClient", "username": username}).get("data", {}).get("client")
    except DynsecNotFound:
        return None


def create_device_client(username: str, password: str) -> None:
    ensure_device_role()
    _one({
        "command": "createClient",
        "username": username,
        "password": password,
        "roles": [{"rolename": DEVICE_ROLE}],
    })


def set_password(username: str, password: str) -> None:
    _one({"command": "setClientPassword", "username": username, "password": password})


def enable_client(username: str) -> None:
    _one({"command": "enableClient", "username": username})


def disable_client(username: str) -> None:
    """Deshabilita el usuario y corta su conexión activa."""
    _one({"command": "disableClient", "username": username})


def delete_client(username: str) -> None:
    _one({"command": "deleteClient", "username": username})
