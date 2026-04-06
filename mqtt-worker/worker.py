# mqtt-worker/worker.py

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import Json
import redis
from dotenv import load_dotenv

# ============================================================
# CARGA DE VARIABLES DE ENTORNO
# ============================================================
# Permite leer .env local en desarrollo y variables del contenedor en producción.
load_dotenv()

# ============================================================
# CONFIG LOGGING
# ============================================================
# Dejamos timestamps en UTC para que todo quede consistente:
# logs del worker, base de datos y correlación con API.
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

# ============================================================
# CONFIG MQTT
# ============================================================
MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# ============================================================
# CONFIG BASE DE DATOS / REDIS
# ============================================================
DB_URL = os.getenv("DATABASE_URL_WORKER") or os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/postgres",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# ============================================================
# CONFIG PRESENCIA / OFFLINE
# ============================================================
# Si un equipo no reporta durante este tiempo, lo marcamos offline.
DEVICE_OFFLINE_TIMEOUT_SECONDS = int(os.getenv("DEVICE_OFFLINE_TIMEOUT_SECONDS", "600"))
OFFLINE_CHECK_INTERVAL_SECONDS = int(os.getenv("OFFLINE_CHECK_INTERVAL_SECONDS", "60"))

# ============================================================
# CLIENTE REDIS
# ============================================================
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


# ============================================================
# HELPERS GENERALES
# ============================================================
def utcnow() -> datetime:
    """
    Devuelve hora actual con zona UTC.
    Tener una única función ayuda a mantener consistencia.
    """
    return datetime.now(timezone.utc)


def connect_db():
    """
    Abre una conexión nueva a PostgreSQL.

    IMPORTANTE:
    No conviene compartir una misma conexión entre múltiples hilos.
    Por eso cada loop/hilo importante usa su propia conexión.
    """
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    return conn


def safe_json_loads(payload_raw: bytes) -> Optional[Any]:
    """
    Intenta decodificar payload como JSON.
    Si no puede, devuelve None.
    """
    try:
        return json.loads(payload_raw.decode("utf-8"))
    except Exception:
        return None


def payload_to_json_compatible(payload: Any) -> Any:
    """
    Convierte payload a algo apto para guardar en JSONB.

    Casos:
    - dict/list/bool/int/float/... -> lo guarda tal cual
    - bytes/string no JSON -> lo envuelve en un dict
    """
    if isinstance(payload, (dict, list, int, float, bool)) or payload is None:
        return payload

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except Exception:
            text = repr(payload)
        return {"raw": text}

    if isinstance(payload, str):
        return {"raw": payload}

    return {"raw": str(payload)}


def parse_device_id_from_topic(topic: str) -> Optional[str]:
    """
    Espera topics del estilo:
    iot/devices/{device_id}/telemetry
    iot/devices/{device_id}/state
    iot/devices/{device_id}/status
    iot/devices/{device_id}/response
    iot/devices/{device_id}/cmd
    """
    parts = topic.split("/")
    if len(parts) >= 3:
        return parts[2]
    return None


# ============================================================
# CREACIÓN / AJUSTE DE TABLAS
# ============================================================
def ensure_tables(conn):
    """
    Crea tablas base operativas y nuevas tablas de auditoría MQTT.

    Mantengo tus tablas:
    - devices
    - commands
    - telemetry

    Agrego:
    - mqtt_events
    - audit_logs

    Nota:
    'audit_logs' la dejo compatible con la futura integración del backend.
    """
    with conn.cursor() as cur:
        # --------------------------------------------------------
        # TABLA devices
        # --------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255),
                status VARCHAR(50) DEFAULT 'offline',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_seen TIMESTAMPTZ
            );
            """
        )

        # --------------------------------------------------------
        # TABLA commands
        # --------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255) REFERENCES devices(device_id),
                command VARCHAR(255) NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # Por compatibilidad con instalaciones previas.
        cur.execute(
            """
            ALTER TABLE commands
            ADD COLUMN IF NOT EXISTS command VARCHAR(255) NOT NULL DEFAULT '';
            """
        )
        cur.execute("ALTER TABLE commands ALTER COLUMN command DROP DEFAULT;")

        # --------------------------------------------------------
        # TABLA telemetry
        # --------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255) REFERENCES devices(device_id),
                timestamp TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            """
        )

        # --------------------------------------------------------
        # TABLA mqtt_events
        # --------------------------------------------------------
        # Esta es la tabla profesional para trazabilidad MQTT.
        # Guarda tanto lo que entra como lo que sale.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mqtt_events (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255),
                direction VARCHAR(20) NOT NULL,      -- inbound / outbound
                topic VARCHAR(500) NOT NULL,
                payload JSONB,
                event_type VARCHAR(100) NOT NULL,   -- telemetry/state/status/response/command_dispatch/...
                processed_ok BOOLEAN NOT NULL DEFAULT TRUE,
                related_command_id INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # Índices útiles para consultas futuras.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mqtt_events_device_created_at
            ON mqtt_events (device_id, created_at DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mqtt_events_event_type
            ON mqtt_events (event_type);
            """
        )

        # --------------------------------------------------------
        # TABLA audit_logs
        # --------------------------------------------------------
        # La dejamos amplia para usarla también desde API más adelante.
        # Acá desde el worker registraremos eventos operativos MQTT.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NULL,
                username VARCHAR(255) NULL,
                action_type VARCHAR(100) NOT NULL,
                device_id VARCHAR(255) NULL,
                payload JSONB NULL,
                result VARCHAR(50) NOT NULL,
                ip_address VARCHAR(255) NULL,
                user_agent TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_device_created_at
            ON audit_logs (device_id, created_at DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_action_type
            ON audit_logs (action_type);
            """
        )


# ============================================================
# FUNCIONES DB - OPERATIVAS
# ============================================================
def upsert_device(conn, device_id: str, status: str, last_seen: datetime):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (device_id, status, last_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (device_id) DO UPDATE SET
                status = EXCLUDED.status,
                last_seen = EXCLUDED.last_seen;
            """,
            (device_id, status, last_seen),
        )


def update_device_status(conn, device_id: str, status: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE devices
            SET status = %s
            WHERE device_id = %s;
            """,
            (status, device_id),
        )


def insert_telemetry(conn, device_id: str, timestamp: datetime, payload: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO telemetry (device_id, timestamp, payload)
            VALUES (%s, %s, %s);
            """,
            (device_id, timestamp, Json(payload)),
        )


def mark_command_sent(conn, command_id: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE commands
            SET status = 'sent'
            WHERE id = %s
            """,
            (command_id,),
        )


def mark_command_failed(conn, command_id: int):
    """
    Marca comando como failed cuando el publish al broker falla.
    Esto mejora mucho la trazabilidad.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE commands
            SET status = 'failed'
            WHERE id = %s
            """,
            (command_id,),
        )


def fetch_pending_commands(conn, limit: int = 50):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, device_id, command
            FROM commands
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT %s;
            """,
            (limit,),
        )
        return cur.fetchall()


# ============================================================
# FUNCIONES DB - AUDITORÍA / TRAZABILIDAD
# ============================================================
def insert_mqtt_event(
    conn,
    *,
    device_id: Optional[str],
    direction: str,
    topic: str,
    payload: Any,
    event_type: str,
    processed_ok: bool,
    related_command_id: Optional[int] = None,
):
    """
    Guarda un evento de comunicación MQTT en tabla separada.
    Esta tabla es la base de la trazabilidad técnica.
    """
    payload_json = payload_to_json_compatible(payload)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mqtt_events (
                device_id,
                direction,
                topic,
                payload,
                event_type,
                processed_ok,
                related_command_id,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                device_id,
                direction,
                topic,
                Json(payload_json),
                event_type,
                processed_ok,
                related_command_id,
                utcnow(),
            ),
        )


def insert_audit_log(
    conn,
    *,
    action_type: str,
    result: str,
    device_id: Optional[str] = None,
    payload: Any = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    """
    Guarda eventos de auditoría de más alto nivel.
    En esta etapa lo usaremos sobre todo para acciones MQTT del worker.
    """
    payload_json = payload_to_json_compatible(payload)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (
                user_id,
                username,
                action_type,
                device_id,
                payload,
                result,
                ip_address,
                user_agent,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                user_id,
                username,
                action_type,
                device_id,
                Json(payload_json) if payload_json is not None else None,
                result,
                ip_address,
                user_agent,
                utcnow(),
            ),
        )


# ============================================================
# PRESENCIA / HEARTBEAT
# ============================================================
def touch_device_presence(conn, device_id: str, status: str = "online"):
    """
    Actualiza presencia del equipo en DB y Redis.

    DB:
    - status
    - last_seen

    Redis:
    - status efímero
    - heartbeat timestamp
    """
    now = utcnow()

    # Actualiza estado persistente
    upsert_device(conn, device_id, status=status, last_seen=now)

    # Cache rápida en Redis para dashboard/API
    redis_client.set(
        f"device:{device_id}:status",
        status,
        ex=DEVICE_OFFLINE_TIMEOUT_SECONDS,
    )

    # Heartbeat separado para debugging / watchdog
    redis_client.set(
        f"device:{device_id}:last_seen_ts",
        str(int(now.timestamp())),
        ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
    )


# ============================================================
# HANDLERS MQTT ENTRANTES
# ============================================================
def handle_telemetry(conn, topic: str, payload_raw: bytes):
    """
    Maneja topics:
    - /telemetry
    - /state

    Lo que hace:
    1. Intenta parsear JSON
    2. Obtiene device_id
    3. Actualiza presencia
    4. Guarda telemetry
    5. Registra mqtt_event inbound
    """
    data = safe_json_loads(payload_raw)
    if data is None:
        logging.warning("Invalid JSON on %s", topic)

        device_id = parse_device_id_from_topic(topic)
        insert_mqtt_event(
            conn,
            device_id=device_id,
            direction="inbound",
            topic=topic,
            payload=payload_raw,
            event_type="mqtt_invalid_json_received",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_invalid_json_received",
            device_id=device_id,
            payload={"topic": topic, "payload": payload_raw.decode("utf-8", errors="replace")},
            result="failed",
        )
        return

    device_id = parse_device_id_from_topic(topic)
    if not device_id:
        logging.warning("Could not parse device_id from topic %s", topic)

        insert_mqtt_event(
            conn,
            device_id=None,
            direction="inbound",
            topic=topic,
            payload=data,
            event_type="mqtt_topic_parse_failed",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_topic_parse_failed",
            device_id=None,
            payload={"topic": topic, "payload": data},
            result="failed",
        )
        return

    timestamp = utcnow()

    # Conservamos timestamp del equipo si viene en payload
    device_ts = data.get("ts")
    if device_ts is not None:
        try:
            data["device_ts"] = float(device_ts)
        except (TypeError, ValueError):
            data["device_ts"] = device_ts

    touch_device_presence(conn, device_id, status="online")
    insert_telemetry(conn, device_id, timestamp, data)

    event_type = "mqtt_state_received" if topic.endswith("/state") else "mqtt_telemetry_received"
    insert_mqtt_event(
        conn,
        device_id=device_id,
        direction="inbound",
        topic=topic,
        payload=data,
        event_type=event_type,
        processed_ok=True,
    )

    logging.info("Telemetry stored for %s", device_id)


def handle_status(conn, topic: str, payload_raw: bytes):
    """
    Maneja mensajes /status.

    No siempre vienen como JSON.
    Normalmente son strings: online, offline, etc.
    """
    status_text = payload_raw.decode("utf-8", errors="replace").strip() or "unknown"
    device_id = parse_device_id_from_topic(topic)

    if not device_id:
        logging.warning("Could not parse device_id from topic %s", topic)

        insert_mqtt_event(
            conn,
            device_id=None,
            direction="inbound",
            topic=topic,
            payload=status_text,
            event_type="mqtt_topic_parse_failed",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_topic_parse_failed",
            payload={"topic": topic, "payload": status_text},
            result="failed",
        )
        return

    normalized = status_text.lower()
    now = utcnow()

    if normalized == "online":
        touch_device_presence(conn, device_id, status="online")
    elif normalized == "offline":
        upsert_device(conn, device_id, status="offline", last_seen=now)
        redis_client.set(
            f"device:{device_id}:status",
            "offline",
            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
        )
        redis_client.set(
            f"device:{device_id}:last_seen_ts",
            str(int(now.timestamp())),
            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
        )
    else:
        upsert_device(conn, device_id, status=normalized, last_seen=now)
        redis_client.set(
            f"device:{device_id}:status",
            normalized,
            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
        )
        redis_client.set(
            f"device:{device_id}:last_seen_ts",
            str(int(now.timestamp())),
            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
        )

    insert_mqtt_event(
        conn,
        device_id=device_id,
        direction="inbound",
        topic=topic,
        payload={"status": normalized},
        event_type="mqtt_status_received",
        processed_ok=True,
    )

    logging.info("Status %s for %s", normalized, device_id)


def handle_response(conn, topic: str, payload_raw: bytes):
    """
    Maneja mensajes /response.

    Estos mensajes son muy valiosos para trazabilidad porque suelen ser
    la respuesta explícita del dispositivo a un comando.
    """
    device_id = parse_device_id_from_topic(topic)
    parsed = safe_json_loads(payload_raw)
    payload = parsed if parsed is not None else payload_raw.decode("utf-8", errors="replace")

    insert_mqtt_event(
        conn,
        device_id=device_id,
        direction="inbound",
        topic=topic,
        payload=payload,
        event_type="mqtt_response_received",
        processed_ok=True,
    )

    logging.info("Response from %s: %s", topic, payload)


# ============================================================
# WATCHDOG DE PRESENCIA
# ============================================================
def presence_watchdog_loop():
    """
    Recorre dispositivos y marca offline los que superan el timeout.

    Usa su propia conexión DB.
    """
    conn = connect_db()

    while True:
        try:
            now_ts = int(time.time())

            with conn.cursor() as cur:
                cur.execute("SELECT device_id, last_seen FROM devices;")
                rows = cur.fetchall()

            for device_id, last_seen in rows:
                if last_seen is None:
                    continue

                age_seconds = now_ts - int(last_seen.timestamp())

                if age_seconds > DEVICE_OFFLINE_TIMEOUT_SECONDS:
                    current_status = redis_client.get(f"device:{device_id}:status")

                    if current_status != "offline":
                        update_device_status(conn, device_id, "offline")
                        redis_client.set(
                            f"device:{device_id}:status",
                            "offline",
                            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
                        )

                        insert_mqtt_event(
                            conn,
                            device_id=device_id,
                            direction="inbound",
                            topic=f"system/devices/{device_id}/presence_watchdog",
                            payload={"age_seconds": age_seconds},
                            event_type="device_marked_offline_by_watchdog",
                            processed_ok=True,
                        )

                        insert_audit_log(
                            conn,
                            action_type="device_marked_offline_by_watchdog",
                            device_id=device_id,
                            payload={"age_seconds": age_seconds},
                            result="success",
                        )

                        logging.warning(
                            "Device %s marked offline (last_seen %ss ago)",
                            device_id,
                            age_seconds,
                        )

        except Exception as exc:
            logging.exception("Presence watchdog error: %s", exc)

        time.sleep(OFFLINE_CHECK_INTERVAL_SECONDS)


# ============================================================
# DESPACHO DE COMANDOS SALIENTES
# ============================================================
def command_dispatch_loop(client: mqtt.Client):
    """
    Busca comandos pendientes en DB y los publica por MQTT.

    MUY IMPORTANTE:
    Esta función usa su propia conexión DB para evitar compartir conexión
    con callbacks MQTT u otros hilos.
    """
    conn = connect_db()

    while True:
        try:
            pending = fetch_pending_commands(conn)

            for cmd_id, device_id, command in pending:
                topic = f"iot/devices/{device_id}/cmd"

                logging.info("Publishing command to %s: %s", topic, command)

                result = client.publish(topic, command)

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_command_sent(conn, cmd_id)

                    insert_mqtt_event(
                        conn,
                        device_id=device_id,
                        direction="outbound",
                        topic=topic,
                        payload={"command": command},
                        event_type="mqtt_command_dispatched",
                        processed_ok=True,
                        related_command_id=cmd_id,
                    )

                    insert_audit_log(
                        conn,
                        action_type="mqtt_command_dispatched",
                        device_id=device_id,
                        payload={
                            "command_id": cmd_id,
                            "topic": topic,
                            "command": command,
                        },
                        result="success",
                    )

                    logging.info("Sent command %s to %s", command, device_id)

                else:
                    mark_command_failed(conn, cmd_id)

                    insert_mqtt_event(
                        conn,
                        device_id=device_id,
                        direction="outbound",
                        topic=topic,
                        payload={
                            "command": command,
                            "mqtt_rc": result.rc,
                        },
                        event_type="mqtt_command_failed",
                        processed_ok=False,
                        related_command_id=cmd_id,
                    )

                    insert_audit_log(
                        conn,
                        action_type="mqtt_command_failed",
                        device_id=device_id,
                        payload={
                            "command_id": cmd_id,
                            "topic": topic,
                            "command": command,
                            "mqtt_rc": result.rc,
                        },
                        result="failed",
                    )

                    logging.error(
                        "Failed to publish command to %s (rc=%s)",
                        device_id,
                        result.rc,
                    )

        except Exception as exc:
            logging.exception("Command dispatch error: %s", exc)

        time.sleep(0.5)


# ============================================================
# CALLBACKS MQTT
# ============================================================
def on_connect(client, userdata, flags, rc):
    """
    Se ejecuta al conectar con el broker.
    """
    if rc == 0:
        logging.info("Connected to MQTT broker")

        client.subscribe("iot/devices/+/telemetry")
        client.subscribe("iot/devices/+/state")
        client.subscribe("iot/devices/+/status")
        client.subscribe("iot/devices/+/response")

        logging.info("Subscribed to device topics")
    else:
        logging.error("Failed to connect to MQTT broker: rc=%s", rc)


def on_message(client, userdata, msg):
    """
    Callback principal de mensajes entrantes.

    Usamos una conexión DB exclusiva para callbacks MQTT.
    """
    conn = userdata["db"]

    try:
        if msg.topic.endswith("/telemetry") or msg.topic.endswith("/state"):
            handle_telemetry(conn, msg.topic, msg.payload)

        elif msg.topic.endswith("/status"):
            handle_status(conn, msg.topic, msg.payload)

        elif msg.topic.endswith("/response"):
            handle_response(conn, msg.topic, msg.payload)

        else:
            # Esto normalmente no debería ocurrir porque nos suscribimos a topics conocidos,
            # pero si pasa, queda registrado.
            device_id = parse_device_id_from_topic(msg.topic)

            insert_mqtt_event(
                conn,
                device_id=device_id,
                direction="inbound",
                topic=msg.topic,
                payload=msg.payload,
                event_type="mqtt_unhandled_topic_received",
                processed_ok=False,
            )

            insert_audit_log(
                conn,
                action_type="mqtt_unhandled_topic_received",
                device_id=device_id,
                payload={
                    "topic": msg.topic,
                    "payload": msg.payload.decode("utf-8", errors="replace"),
                },
                result="failed",
            )

            logging.warning("Unhandled MQTT topic: %s", msg.topic)

    except Exception as exc:
        logging.exception("Error handling message on %s: %s", msg.topic, exc)

        # Intentamos igualmente dejar evidencia del problema.
        try:
            device_id = parse_device_id_from_topic(msg.topic)
            insert_mqtt_event(
                conn,
                device_id=device_id,
                direction="inbound",
                topic=msg.topic,
                payload=msg.payload,
                event_type="mqtt_message_processing_error",
                processed_ok=False,
            )
            insert_audit_log(
                conn,
                action_type="mqtt_message_processing_error",
                device_id=device_id,
                payload={
                    "topic": msg.topic,
                    "error": str(exc),
                    "payload": msg.payload.decode("utf-8", errors="replace"),
                },
                result="failed",
            )
        except Exception:
            logging.exception("Could not persist MQTT processing error")


# ============================================================
# MAIN
# ============================================================
def main():
    """
    Punto de entrada del worker.

    Estrategia:
    - Conexión DB #1: bootstrap de tablas
    - Conexión DB #2: callbacks MQTT
    - Conexión DB #3: command dispatch loop
    - Conexión DB #4: watchdog loop
    """
    bootstrap_conn = connect_db()
    ensure_tables(bootstrap_conn)
    bootstrap_conn.close()

    callback_conn = connect_db()

    client = mqtt.Client(userdata={"db": callback_conn})

    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    dispatcher = threading.Thread(
        target=command_dispatch_loop,
        args=(client,),
        daemon=True,
    )
    dispatcher.start()

    watchdog = threading.Thread(
        target=presence_watchdog_loop,
        daemon=True,
    )
    watchdog.start()

    client.loop_forever()


if __name__ == "__main__":
    main()