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
load_dotenv()


# ============================================================
# CONFIG LOGGING
# ============================================================
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
    return datetime.now(timezone.utc)


def connect_db():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    return conn


def ensure_connection(conn):
    try:
        if conn is None or conn.closed != 0:
            logging.warning("DB connection is closed or missing. Reconnecting...")
            return connect_db()

        with conn.cursor() as cur:
            cur.execute("SELECT 1;")

        return conn

    except Exception as exc:
        logging.warning("DB connection check failed. Reconnecting... (%s)", exc)

        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

        return connect_db()


def safe_json_loads(payload_raw: bytes) -> Optional[Any]:
    try:
        return json.loads(payload_raw.decode("utf-8"))
    except Exception:
        return None


def payload_to_json_compatible(payload: Any) -> Any:
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
    Topics esperados:
    iot/devices/{device_id}/telemetry
    iot/devices/{device_id}/state
    iot/devices/{device_id}/status
    iot/devices/{device_id}/response
    iot/devices/{device_id}/event
    iot/devices/{device_id}/cmd
    """
    parts = topic.split("/")
    if len(parts) >= 3:
        return parts[2]
    return None


def normalize_bool(value: Any) -> Optional[bool]:
    """
    Normaliza booleanos que puedan venir desde Arduino como:
    true/false, 1/0, "1"/"0", "true"/"false".
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return bool(value)

    if isinstance(value, str):
        value_normalized = value.strip().lower()
        if value_normalized in ("1", "true", "yes", "on"):
            return True
        if value_normalized in ("0", "false", "no", "off"):
            return False

    return None


def normalize_door_sensor(value: Any) -> Optional[str]:
    """
    Normaliza estado de sensor magnético.
    Valores finales permitidos:
    - open
    - closed
    """
    if value is None:
        return None

    text = str(value).strip().lower()

    if text in ("open", "opened", "abierta", "abierto", "1"):
        return "open"

    if text in ("closed", "close", "cerrada", "cerrado", "0"):
        return "closed"

    return text


# ============================================================
# CREACIÓN / AJUSTE DE TABLAS
# ============================================================
def ensure_tables(conn):
    """
    Crea y ajusta tablas necesarias.

    Este worker mantiene:
    - devices
    - commands
    - telemetry
    - mqtt_events
    - audit_logs
    - access_events

    Además agrega columnas de estado actual en devices:
    - last_door_opened
    - last_door_event_at
    - last_door_reader
    - last_access_event_type
    - last_card_uid
    - last_access_authorized
    - last_door_sensor_state
    - last_door_sensor_at

    IMPORTANTE:
    access_events es historial.
    devices guarda el estado actual para dashboard.
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

        # Estado actual de control de acceso.
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_opened BOOLEAN NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_event_at TIMESTAMPTZ NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_reader VARCHAR(50) NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_access_event_type VARCHAR(50) NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_card_uid VARCHAR(100) NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_access_authorized BOOLEAN NULL;
            """
        )

        # Estado real del sensor magnético.
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_sensor_state VARCHAR(20) NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_sensor_at TIMESTAMPTZ NULL;
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
                command TEXT NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        cur.execute(
            """
            ALTER TABLE commands
            ADD COLUMN IF NOT EXISTS command TEXT NOT NULL DEFAULT '';
            """
        )
        cur.execute("ALTER TABLE commands ALTER COLUMN command DROP DEFAULT;")

        cur.execute(
            """
            ALTER TABLE commands
            ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE commands
            ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE commands
            ADD COLUMN IF NOT EXISTS response_payload JSONB NULL;
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commands_status_created_at
            ON commands (status, created_at ASC);
            """
        )

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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mqtt_events (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255),
                direction VARCHAR(20) NOT NULL,
                topic VARCHAR(500) NOT NULL,
                payload JSONB,
                event_type VARCHAR(100) NOT NULL,
                processed_ok BOOLEAN NOT NULL DEFAULT TRUE,
                related_command_id INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

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
                channel_type VARCHAR(50) NULL,
                channel_identifier VARCHAR(255) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        cur.execute(
            """
            ALTER TABLE audit_logs
            ADD COLUMN IF NOT EXISTS channel_type VARCHAR(50) NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE audit_logs
            ADD COLUMN IF NOT EXISTS channel_identifier VARCHAR(255) NULL;
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
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_channel_type
            ON audit_logs (channel_type);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_channel_identifier
            ON audit_logs (channel_identifier);
            """
        )

        # --------------------------------------------------------
        # TABLA access_events
        # --------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS access_events (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(255),
                reader VARCHAR(50),
                event_type VARCHAR(50),
                card_uid VARCHAR(100),
                authorized BOOLEAN,
                door_opened BOOLEAN,
                payload JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_access_events_device_created_at
            ON access_events (device_id, created_at DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_access_events_reader_created_at
            ON access_events (reader, created_at DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_access_events_card_uid
            ON access_events (card_uid);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_access_events_event_type
            ON access_events (event_type);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_access_events_authorized
            ON access_events (authorized);
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


def mark_command_dispatched(conn, command_id: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE commands
            SET status = 'dispatched',
                dispatched_at = %s
            WHERE id = %s
              AND status = 'pending';
            """,
            (utcnow(), command_id),
        )


def mark_command_failed(conn, command_id: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE commands
            SET status = 'failed'
            WHERE id = %s;
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
    channel_type: Optional[str] = None,
    channel_identifier: Optional[str] = None,
):
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
                channel_type,
                channel_identifier,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
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
                channel_type,
                channel_identifier,
                utcnow(),
            ),
        )


def insert_access_event(
    conn,
    *,
    device_id: Optional[str],
    reader: Optional[str],
    event_type: Optional[str],
    card_uid: Optional[str],
    authorized: Optional[bool],
    door_opened: Optional[bool],
    door_sensor: Optional[str],
    payload: Any,
):
    """
    Guarda eventos específicos de control de acceso.

    También actualiza devices con el estado actual.

    Diferencia importante:

    door_opened:
        indica que Arduino activó relé/apertura.

    door_sensor:
        indica estado real del sensor magnético:
        - open
        - closed

    Ejemplos payload:

    ACCESS:
    {
        "event": "access",
        "reader": "L1",
        "card_uid": "5033541",
        "authorized": true,
        "door_opened": true
    }

    DOOR STATE:
    {
        "event": "door_state",
        "reader": "L1",
        "door_sensor": "open"
    }
    """
    payload_json = payload_to_json_compatible(payload)
    now = utcnow()

    with conn.cursor() as cur:
        # ========================================================
        # INSERT HISTÓRICO
        # ========================================================
        cur.execute(
            """
            INSERT INTO access_events (
                device_id,
                reader,
                event_type,
                card_uid,
                authorized,
                door_opened,
                payload,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                device_id,
                reader,
                event_type,
                card_uid,
                authorized,
                door_opened,
                Json(payload_json),
                now,
            ),
        )

        # ========================================================
        # ESTADO ACTUAL POR RELÉ / ACCESO
        # ========================================================
        if device_id and isinstance(door_opened, bool):
            cur.execute(
                """
                UPDATE devices
                SET last_door_opened = %s,
                    last_door_event_at = %s,
                    last_door_reader = %s,
                    last_access_event_type = %s,
                    last_card_uid = %s,
                    last_access_authorized = %s
                WHERE device_id = %s;
                """,
                (
                    door_opened,
                    now,
                    reader,
                    event_type,
                    card_uid,
                    authorized if isinstance(authorized, bool) else None,
                    device_id,
                ),
            )

        # ========================================================
        # ESTADO ACTUAL REAL DEL SENSOR MAGNÉTICO
        # ========================================================
        if device_id and door_sensor:
            cur.execute(
                """
                UPDATE devices
                SET last_door_sensor_state = %s,
                    last_door_sensor_at = %s,
                    last_door_reader = %s,
                    last_access_event_type = %s
                WHERE device_id = %s;
                """,
                (
                    door_sensor,
                    now,
                    reader,
                    event_type,
                    device_id,
                ),
            )


# ============================================================
# PRESENCIA / HEARTBEAT
# ============================================================
def touch_device_presence(conn, device_id: str, status: str = "online"):
    now = utcnow()

    upsert_device(conn, device_id, status=status, last_seen=now)

    try:
        redis_client.set(
            f"device:{device_id}:status",
            status,
            ex=DEVICE_OFFLINE_TIMEOUT_SECONDS,
        )
        redis_client.set(
            f"device:{device_id}:last_seen_ts",
            str(int(now.timestamp())),
            ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
        )
    except Exception as exc:
        logging.exception("Redis presence update failed for %s: %s", device_id, exc)


# ============================================================
# HANDLERS MQTT ENTRANTES
# ============================================================
def handle_telemetry(conn, topic: str, payload_raw: bytes):
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
            payload={
                "topic": topic,
                "payload": payload_raw.decode("utf-8", errors="replace"),
            },
            result="failed",
        )
        return

    if not isinstance(data, dict):
        logging.warning("Unexpected JSON type on %s: %s", topic, type(data).__name__)

        device_id = parse_device_id_from_topic(topic)
        insert_mqtt_event(
            conn,
            device_id=device_id,
            direction="inbound",
            topic=topic,
            payload=data,
            event_type="mqtt_unexpected_payload_type",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_unexpected_payload_type",
            device_id=device_id,
            payload={"topic": topic, "payload": data},
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

    logging.info(
        "Telemetry stored | device=%s payload=%s",
        device_id,
        json.dumps(data, ensure_ascii=False),
    )


def handle_status(conn, topic: str, payload_raw: bytes):
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

        try:
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
        except Exception as exc:
            logging.exception("Redis offline status update failed for %s: %s", device_id, exc)

    else:
        upsert_device(conn, device_id, status=normalized, last_seen=now)

        try:
            redis_client.set(
                f"device:{device_id}:status",
                normalized,
                ex=DEVICE_OFFLINE_TIMEOUT_SECONDS,
            )
            redis_client.set(
                f"device:{device_id}:last_seen_ts",
                str(int(now.timestamp())),
                ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
            )
        except Exception as exc:
            logging.exception("Redis custom status update failed for %s: %s", device_id, exc)

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


def handle_event(conn, topic: str, payload_raw: bytes):
    """
    Maneja eventos desacoplados del sistema IoT.

    Topic esperado:
    iot/devices/{device_id}/event

    Eventos soportados actualmente:

    1) access
    {
        "event": "access",
        "reader": "L1",
        "card_uid": "5033541",
        "authorized": true,
        "door_opened": true
    }

    2) door_request
    {
        "event": "door_request",
        "source": "button",
        "reader": "L2",
        "door_opened": true
    }

    3) door_state
    {
        "event": "door_state",
        "reader": "L1",
        "door_sensor": "open"
    }

    4) mode / enroll / error
    También se guardan como eventos generales.
    """
    data = safe_json_loads(payload_raw)
    device_id = parse_device_id_from_topic(topic)

    if data is None:
        logging.warning("Invalid JSON event on %s", topic)

        insert_mqtt_event(
            conn,
            device_id=device_id,
            direction="inbound",
            topic=topic,
            payload=payload_raw,
            event_type="mqtt_event_invalid_json_received",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_event_invalid_json_received",
            device_id=device_id,
            payload={
                "topic": topic,
                "payload": payload_raw.decode("utf-8", errors="replace"),
            },
            result="failed",
        )
        return

    if not isinstance(data, dict):
        logging.warning("Unexpected event JSON type on %s: %s", topic, type(data).__name__)

        insert_mqtt_event(
            conn,
            device_id=device_id,
            direction="inbound",
            topic=topic,
            payload=data,
            event_type="mqtt_event_unexpected_payload_type",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_event_unexpected_payload_type",
            device_id=device_id,
            payload={"topic": topic, "payload": data},
            result="failed",
        )
        return

    if not device_id:
        logging.warning("Could not parse device_id from event topic %s", topic)

        insert_mqtt_event(
            conn,
            device_id=None,
            direction="inbound",
            topic=topic,
            payload=data,
            event_type="mqtt_event_topic_parse_failed",
            processed_ok=False,
        )
        insert_audit_log(
            conn,
            action_type="mqtt_event_topic_parse_failed",
            device_id=None,
            payload={"topic": topic, "payload": data},
            result="failed",
        )
        return

    # Todo evento válido indica que el dispositivo está vivo.
    touch_device_presence(conn, device_id, status="online")

    event_name = data.get("event") or data.get("event_type") or "unknown"
    reader = data.get("reader")
    card_uid = data.get("card_uid")

    authorized = normalize_bool(data.get("authorized"))
    door_opened = normalize_bool(data.get("door_opened"))
    door_sensor = normalize_door_sensor(
        data.get("door_sensor")
        or data.get("state")
)

    insert_access_event(
        conn,
        device_id=device_id,
        reader=reader,
        event_type=event_name,
        card_uid=card_uid,
        authorized=authorized,
        door_opened=door_opened,
        door_sensor=door_sensor,
        payload=data,
    )

    mqtt_event_type = (
        "access_event_received"
        if event_name == "access"
        else "mqtt_device_event_received"
    )

    insert_mqtt_event(
        conn,
        device_id=device_id,
        direction="inbound",
        topic=topic,
        payload=data,
        event_type=mqtt_event_type,
        processed_ok=True,
    )

    insert_audit_log(
        conn,
        action_type="access_event" if event_name == "access" else "mqtt_device_event_received",
        device_id=device_id,
        payload=data,
        result="success",
    )

    logging.info(
        "Device event stored | device=%s event=%s reader=%s payload=%s",
        device_id,
        event_name,
        reader,
        json.dumps(data, ensure_ascii=False),
    )


def handle_response(conn, topic: str, payload_raw: bytes):
    device_id = parse_device_id_from_topic(topic)
    parsed = safe_json_loads(payload_raw)
    payload = parsed if parsed is not None else payload_raw.decode("utf-8", errors="replace")

    if device_id:
        touch_device_presence(conn, device_id, status="online")

    insert_mqtt_event(
        conn,
        device_id=device_id,
        direction="inbound",
        topic=topic,
        payload=payload,
        event_type="mqtt_response_received",
        processed_ok=True,
    )

    insert_audit_log(
        conn,
        action_type="mqtt_response_received",
        device_id=device_id,
        payload=payload,
        result="success",
    )

    logging.info("Response from %s: %s", topic, payload)


# ============================================================
# WATCHDOG DE PRESENCIA
# ============================================================
def presence_watchdog_loop():
    conn = None

    while True:
        try:
            conn = ensure_connection(conn)
            now_ts = int(time.time())

            with conn.cursor() as cur:
                cur.execute("SELECT device_id, last_seen FROM devices;")
                rows = cur.fetchall()

            for device_id, last_seen in rows:
                if last_seen is None:
                    continue

                age_seconds = now_ts - int(last_seen.timestamp())

                if age_seconds > DEVICE_OFFLINE_TIMEOUT_SECONDS:
                    current_status = None

                    try:
                        current_status = redis_client.get(f"device:{device_id}:status")
                    except Exception as exc:
                        logging.exception("Redis read failed for %s: %s", device_id, exc)

                    if current_status != "offline":
                        update_device_status(conn, device_id, "offline")

                        try:
                            redis_client.set(
                                f"device:{device_id}:status",
                                "offline",
                                ex=max(DEVICE_OFFLINE_TIMEOUT_SECONDS * 4, 120),
                            )
                        except Exception as exc:
                            logging.exception("Redis offline write failed for %s: %s", device_id, exc)

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

        except psycopg2.InterfaceError as exc:
            logging.exception("Presence watchdog DB interface error: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None

        except psycopg2.OperationalError as exc:
            logging.exception("Presence watchdog DB operational error: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None

        except Exception as exc:
            logging.exception("Presence watchdog error: %s", exc)

        time.sleep(OFFLINE_CHECK_INTERVAL_SECONDS)


# ============================================================
# DESPACHO DE COMANDOS SALIENTES
# ============================================================
def command_dispatch_loop(client: mqtt.Client):
    conn = None

    while True:
        try:
            conn = ensure_connection(conn)
            pending = fetch_pending_commands(conn)

            for cmd_id, device_id, command in pending:
                topic = f"iot/devices/{device_id}/cmd"

                parsed_command = safe_json_loads(command.encode("utf-8"))
                if parsed_command is None:
                    mark_command_failed(conn, cmd_id)

                    insert_mqtt_event(
                        conn,
                        device_id=device_id,
                        direction="outbound",
                        topic=topic,
                        payload={"raw_command": command},
                        event_type="mqtt_command_invalid_payload",
                        processed_ok=False,
                        related_command_id=cmd_id,
                    )

                    insert_audit_log(
                        conn,
                        action_type="mqtt_command_invalid_payload",
                        device_id=device_id,
                        payload={
                            "command_id": cmd_id,
                            "topic": topic,
                            "raw_command": command,
                        },
                        result="failed",
                    )

                    logging.error(
                        "Invalid command JSON | command_id=%s device=%s payload=%s",
                        cmd_id,
                        device_id,
                        command,
                    )
                    continue

                payload_text = json.dumps(parsed_command, ensure_ascii=False)

                logging.info(
                    "Publishing command | command_id=%s topic=%s payload=%s",
                    cmd_id,
                    topic,
                    payload_text,
                )

                result = client.publish(topic, payload_text)

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_command_dispatched(conn, cmd_id)

                    insert_mqtt_event(
                        conn,
                        device_id=device_id,
                        direction="outbound",
                        topic=topic,
                        payload=parsed_command,
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
                            "command_payload": parsed_command,
                        },
                        result="success",
                    )

                    logging.info("Dispatched command_id=%s to %s", cmd_id, device_id)

                else:
                    mark_command_failed(conn, cmd_id)

                    insert_mqtt_event(
                        conn,
                        device_id=device_id,
                        direction="outbound",
                        topic=topic,
                        payload={
                            "command_id": cmd_id,
                            "command_payload": parsed_command,
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
                            "command_payload": parsed_command,
                            "mqtt_rc": result.rc,
                        },
                        result="failed",
                    )

                    logging.error(
                        "Failed to publish command_id=%s to %s rc=%s",
                        cmd_id,
                        device_id,
                        result.rc,
                    )

        except psycopg2.InterfaceError as exc:
            logging.exception("Command dispatch DB interface error: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None

        except psycopg2.OperationalError as exc:
            logging.exception("Command dispatch DB operational error: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None

        except Exception as exc:
            logging.exception("Command dispatch error: %s", exc)

        time.sleep(0.5)


# ============================================================
# CALLBACKS MQTT
# ============================================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT broker")

        client.subscribe("iot/devices/+/telemetry")
        client.subscribe("iot/devices/+/state")
        client.subscribe("iot/devices/+/status")
        client.subscribe("iot/devices/+/response")
        client.subscribe("iot/devices/+/event")

        logging.info("Subscribed to device topics")
    else:
        logging.error("Failed to connect to MQTT broker: rc=%s", rc)


def on_message(client, userdata, msg):
    try:
        userdata["db"] = ensure_connection(userdata.get("db"))
        conn = userdata["db"]

        if msg.topic.endswith("/telemetry") or msg.topic.endswith("/state"):
            handle_telemetry(conn, msg.topic, msg.payload)

        elif msg.topic.endswith("/status"):
            handle_status(conn, msg.topic, msg.payload)

        elif msg.topic.endswith("/response"):
            handle_response(conn, msg.topic, msg.payload)

        elif msg.topic.endswith("/event"):
            handle_event(conn, msg.topic, msg.payload)

        else:
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

    except psycopg2.InterfaceError as exc:
        logging.exception("MQTT callback DB interface error on %s: %s", msg.topic, exc)
        try:
            if userdata.get("db") is not None:
                userdata["db"].close()
        except Exception:
            pass
        userdata["db"] = None

    except psycopg2.OperationalError as exc:
        logging.exception("MQTT callback DB operational error on %s: %s", msg.topic, exc)
        try:
            if userdata.get("db") is not None:
                userdata["db"].close()
        except Exception:
            pass
        userdata["db"] = None

    except Exception as exc:
        logging.exception("Error handling message on %s: %s", msg.topic, exc)

        try:
            userdata["db"] = ensure_connection(userdata.get("db"))
            conn = userdata["db"]

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