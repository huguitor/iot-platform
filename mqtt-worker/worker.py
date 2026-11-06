# mqtt-worker/worker.py

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
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
# client_id fijo + clean_session=False: el broker retiene los mensajes
# mientras el worker esta caido y se los entrega al reconectar.
MQTT_CLIENT_ID = os.getenv("MQTT_WORKER_CLIENT_ID", "iot-platform-mqtt-worker")


# ============================================================
# CONFIG BASE DE DATOS / REDIS
# ============================================================
DB_URL = os.getenv("DATABASE_URL_WORKER") or os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/postgres",
)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


# ============================================================
# EVENTOS TECNICOS: mqtt_events + auditoria, sin historial de acceso
# ============================================================
NON_HISTORY_EVENTS = {"list", "stats", "stats_reset", "boot"}


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
        value = json.loads(payload_raw.decode("utf-8"))
        def check_finite(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Non-finite JSON number")
            if isinstance(item, dict):
                for child in item.values():
                    check_finite(child)
            elif isinstance(item, list):
                for child in item:
                    check_finite(child)
        check_finite(value)
        return value
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
    import re
    parts = topic.split("/")
    if (len(parts) == 4 and parts[:2] == ["iot", "devices"]
            and re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", parts[2])):
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
    occurred_at: Optional[datetime] = None,
    update_current: bool = True,
):
    """
    Guarda eventos específicos de control de acceso.

    occurred_at conserva la hora estimada del evento.
    Los eventos atrasados no reemplazan un estado de fecha posterior.

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
    now = occurred_at or utcnow()

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
        if update_current and device_id and isinstance(door_opened, bool):
            cur.execute(
                """
                UPDATE devices
                SET last_door_opened = %(opened)s,
                    last_door_event_at = %(event_at)s,
                    last_door_reader = CASE WHEN last_door_sensor_at IS NULL OR last_door_sensor_at <= %(event_at)s THEN %(reader)s ELSE last_door_reader END,
                    last_access_event_type = CASE WHEN last_door_sensor_at IS NULL OR last_door_sensor_at <= %(event_at)s THEN %(event_type)s ELSE last_access_event_type END,
                    last_card_uid = %(card)s,
                    last_access_authorized = %(authorized)s
                WHERE device_id = %(device)s
                  AND (last_door_event_at IS NULL OR last_door_event_at <= %(event_at)s);
                """,
                {"opened": door_opened, "event_at": now, "reader": reader,
                 "event_type": event_type, "card": card_uid,
                 "authorized": authorized if isinstance(authorized, bool) else None,
                 "device": device_id},
            )

        # ========================================================
        # ESTADO ACTUAL REAL DEL SENSOR MAGNÉTICO
        # ========================================================
        if update_current and device_id and door_sensor:
            cur.execute(
                """
                UPDATE devices
                SET last_door_sensor_state = %(sensor)s,
                    last_door_sensor_at = %(event_at)s,
                    last_door_reader = CASE WHEN last_door_event_at IS NULL OR last_door_event_at <= %(event_at)s THEN %(reader)s ELSE last_door_reader END,
                    last_access_event_type = CASE WHEN last_door_event_at IS NULL OR last_door_event_at <= %(event_at)s THEN %(event_type)s ELSE last_access_event_type END
                WHERE device_id = %(device)s
                  AND (last_door_sensor_at IS NULL OR last_door_sensor_at <= %(event_at)s);
                """,
                {"sensor": door_sensor, "event_at": now, "reader": reader,
                 "event_type": event_type, "device": device_id},
            )


# ============================================================
# PRESENCIA / HEARTBEAT
# ============================================================
def touch_device_presence(conn, device_id: str, status: str = "online"):
    now = utcnow()

    upsert_device(conn, device_id, status=status, last_seen=now)
    cache_device_presence(device_id, status, now)


def cache_device_presence(device_id, status, now):

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


def event_timing(data, received_at):
    delay = data.get("delay_s", 0)
    valid = (type(delay) in (int, float) and 0 <= delay <= 30 * 86400
             and math.isfinite(delay))
    return (received_at - timedelta(seconds=delay) if valid else received_at,
            valid, delay if valid else None)


def handle_event(conn, topic: str, payload_raw: bytes, retained=False):
    """Atomic domain effects; stable boot_id + event_id allow durable deduplication.

    Never use equal payloads, MQTT packet IDs or DUP alone as event identity.
    Events without stable identity remain distinct to avoid losing real accesses.
    """
    data = safe_json_loads(payload_raw)
    device_id = parse_device_id_from_topic(topic)
    identity = None
    if device_id and isinstance(data, dict):
        boot, event = data.get("boot_id"), data.get("event_id")
        if (isinstance(boot, str) and 0 < len(boot) <= 120
                and type(event) in (str, int) and 0 < len(str(event)) <= 120):
            identity = (boot, event)
    with conn:
        if identity:
            boot, event = identity
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            (json.dumps([device_id, boot, event]),))
                cur.execute(
                    """SELECT payload FROM mqtt_events
                       WHERE device_id = %s AND topic = %s AND direction = 'inbound'
                         AND processed_ok AND event_type IN
                             ('access_event_received', 'mqtt_device_event_received')
                         AND payload->'boot_id' = %s AND payload->'event_id' = %s
                       ORDER BY id LIMIT 1""",
                    (device_id, topic, Json(boot), Json(event)),
                )
                previous = cur.fetchone()
            if previous:
                # Delay can change on retransmission; the event itself must not.
                canonical = lambda value: {k: v for k, v in value.items() if k != "delay_s"}
                conflict = canonical(previous[0]) != canonical(data)
                kind = "mqtt_event_identity_conflict" if conflict else "mqtt_duplicate_event_received"
                insert_mqtt_event(conn, device_id=device_id, direction="inbound",
                                  topic=topic, payload=data, event_type=kind,
                                  processed_ok=not conflict)
                insert_audit_log(conn, device_id=device_id, action_type=kind,
                                 payload=data, result="failed" if conflict else "duplicate")
                return
        live_at = _handle_event(conn, topic, payload_raw, retained=retained)
    # A failed database transaction must not leave a phantom presence in Redis.
    if live_at is not None:
        cache_device_presence(device_id, "online", live_at)


def _handle_event(conn, topic: str, payload_raw: bytes, retained=False):
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

    event_name = data.get("event") or data.get("event_type") or "unknown"
    reader, card_uid = data.get("reader"), data.get("card_uid")
    if (not isinstance(event_name, str) or len(event_name) > 100
            or (reader is not None and (not isinstance(reader, str) or len(reader) > 50))
            or (card_uid is not None and (not isinstance(card_uid, str) or len(card_uid) > 100))):
        insert_mqtt_event(conn, device_id=device_id, direction="inbound", topic=topic,
                          payload=data, event_type="mqtt_event_invalid_fields", processed_ok=False)
        insert_audit_log(conn, device_id=device_id, action_type="mqtt_event_invalid_fields",
                         payload=data, result="failed")
        return

    received_at = utcnow()
    occurred_at, timing_valid, delay = event_timing(data, received_at)
    upsert_device_if_missing(conn, device_id)
    if timing_valid and delay == 0 and not retained:
        upsert_device(conn, device_id, status="online", last_seen=received_at)

    authorized = normalize_bool(data.get("authorized"))
    door_opened = normalize_bool(data.get("door_opened"))
    door_sensor = normalize_door_sensor(
        data.get("door_sensor")
        or data.get("state")
)
    if door_sensor not in ("open", "closed"):
        door_sensor = None

    is_history = event_name not in NON_HISTORY_EVENTS

    if event_name == "boot":
        save_device_info(conn, device_id, data, observed_at=occurred_at,
                         only_if_empty=retained or not timing_valid)

    if is_history:
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
            occurred_at=occurred_at,
            update_current=timing_valid and not retained,
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

    # Technical events also remain auditable, without pretending to be access events.
    insert_audit_log(
        conn,
        action_type="access_event" if event_name == "access" else "mqtt_device_event_received",
        device_id=device_id,
        payload={"event": data, "ingest": {
            "received_at": received_at.isoformat(), "occurred_at": occurred_at.isoformat(),
            "timing_valid": timing_valid, "retained": bool(retained),
        }},
        result="success" if timing_valid else "invalid_event_delay",
    )

    logging.info(
        "Device event stored | device=%s event=%s reader=%s payload=%s",
        device_id,
        event_name,
        reader,
        json.dumps(data, ensure_ascii=False),
    )
    return received_at if timing_valid and delay == 0 and not retained else None


def save_device_info(conn, device_id: str, data: dict, observed_at=None, only_if_empty=False):
    """Merge partial metadata, preserving newer observations and omitted values."""
    cap = data.get("cap")
    cap = cap if type(cap) is int and 0 <= cap <= 2147483647 else None
    def version(key):
        value = str(data[key]) if data.get(key) is not None else None
        return value if value and len(value) <= 50 else None
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE devices
            SET fw_version = COALESCE(%(fw)s, fw_version),
                gateway_fw_version = COALESCE(%(gateway)s, gateway_fw_version),
                card_capacity = COALESCE(%(capacity)s, card_capacity),
                info = COALESCE(info, '{}'::jsonb) || %(info)s,
                info_at = %(observed)s
            WHERE device_id = %(device)s
              AND (info_at IS NULL OR (NOT %(empty_only)s AND info_at <= %(observed)s));
            """,
            {"fw": version("fw"), "gateway": version("gateway_fw"), "capacity": cap,
             "info": Json(data), "observed": observed_at or utcnow(),
             "device": device_id, "empty_only": bool(only_if_empty)},
        )


def handle_info(conn, topic: str, payload_raw: bytes, retained=False):
    """Retained metadata does not imply presence or replace a newer observation."""
    device_id = parse_device_id_from_topic(topic)
    data = safe_json_loads(payload_raw)
    valid = bool(device_id) and isinstance(data, dict)
    if valid:
        upsert_device_if_missing(conn, device_id)
        save_device_info(conn, device_id, data, only_if_empty=retained)
    insert_mqtt_event(conn, device_id=device_id, direction="inbound", topic=topic,
                      payload=data if valid else payload_raw,
                      event_type="mqtt_info_received" if valid else "mqtt_invalid_info_received",
                      processed_ok=valid)
    insert_audit_log(conn, device_id=device_id, action_type="mqtt_info_received",
                     payload={"info": data if valid else payload_to_json_compatible(payload_raw),
                              "retained": bool(retained)}, result="success" if valid else "failed")


def upsert_device_if_missing(conn, device_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO devices (device_id, status) VALUES (%s, 'offline') ON CONFLICT (device_id) DO NOTHING;",
            (device_id,),
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
HEARTBEAT_TOPIC = "iot/platform/worker/heartbeat"
HEARTBEAT_INTERVAL_SECONDS = 10
# Se activa recien cuando el broker confirma (SUBACK) la suscripcion a /event,
# y se apaga al desconectarse: el gateway solo vacia su cola si hay heartbeat.
heartbeat_ready = threading.Event()
event_sub_mid = {"mid": None}


def send_heartbeat_if_ready(client):
    if not heartbeat_ready.is_set():
        return False
    result = client.publish(HEARTBEAT_TOPIC, json.dumps({"ts": int(time.time())}),
                            qos=0, retain=False)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        logging.warning("Heartbeat not queued (rc=%s)", result.rc)
        return False
    return True


def heartbeat_loop(client: mqtt.Client):
    while True:
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            send_heartbeat_if_ready(client)
        except Exception:
            logging.exception("Heartbeat publish failed")


def on_subscribe(client, userdata, mid, granted_qos):
    if mid is None or mid != event_sub_mid["mid"]:
        return
    if list(granted_qos) != [1]:
        heartbeat_ready.clear()
        logging.error("Event subscription did not grant QoS 1; heartbeat disabled")
        return
    heartbeat_ready.set()
    logging.info("Event subscription confirmed at QoS 1; heartbeat enabled")


def on_disconnect(client, userdata, rc):
    heartbeat_ready.clear()
    logging.warning("Disconnected from MQTT broker (rc=%s); heartbeat disabled", rc)


def on_connect(client, userdata, flags, rc):
    heartbeat_ready.clear()
    event_sub_mid["mid"] = None
    if rc == 0:
        logging.info("Connected to MQTT broker")

        client.subscribe("iot/devices/+/telemetry")
        client.subscribe("iot/devices/+/state")
        client.subscribe("iot/devices/+/status")
        client.subscribe("iot/devices/+/response")
        # QoS 1: los eventos de acceso no se deben perder (sesion persistente)
        result, mid = client.subscribe("iot/devices/+/event", qos=1)
        event_sub_mid["mid"] = mid if result == mqtt.MQTT_ERR_SUCCESS else None
        client.subscribe("iot/devices/+/info")

        logging.info("Subscribed to device topics")
    else:
        logging.error("Failed to connect to MQTT broker: rc=%s", rc)


def on_message(client, userdata, msg):
    try:
        userdata["db"] = ensure_connection(userdata.get("db"))
        conn = userdata["db"]
        if msg.topic.endswith("/event"):
            handle_event(conn, msg.topic, msg.payload, retained=msg.retain)
        else:
            with conn:
                if msg.topic.endswith("/telemetry") or msg.topic.endswith("/state"):
                    handle_telemetry(conn, msg.topic, msg.payload)
                elif msg.topic.endswith("/status"):
                    handle_status(conn, msg.topic, msg.payload)
                elif msg.topic.endswith("/response"):
                    handle_response(conn, msg.topic, msg.payload)
                elif msg.topic.endswith("/info"):
                    handle_info(conn, msg.topic, msg.payload, retained=msg.retain)
                else:
                    insert_mqtt_event(conn, device_id=parse_device_id_from_topic(msg.topic),
                                      direction="inbound", topic=msg.topic, payload=msg.payload,
                                      event_type="mqtt_unhandled_topic_received", processed_ok=False)
                    insert_audit_log(conn, device_id=parse_device_id_from_topic(msg.topic),
                                     action_type="mqtt_unhandled_topic_received",
                                     payload={"topic": msg.topic, "payload": payload_to_json_compatible(msg.payload)},
                                     result="failed")
    except Exception:
        # Paho 1.6 acknowledges QoS 1 after this callback returns successfully.
        # Propagate failures so the process exits and persistent-session redelivery
        # can retry; swallowing a DB error would acknowledge an uncommitted event.
        logging.exception("MQTT processing failed; stopping before acknowledgement")
        raise


def validate_firmware_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='devices'""")
        columns = {row[0] for row in cur.fetchall()}
    required = {"fw_version", "gateway_fw_version", "card_capacity", "info", "info_at"}
    if not required <= columns:
        raise RuntimeError("Apply Alembic 20261104_01 before starting this worker")


# ============================================================
# MAIN
# ============================================================
def main():
    bootstrap_conn = connect_db()
    validate_firmware_schema(bootstrap_conn)
    ensure_tables(bootstrap_conn)
    bootstrap_conn.close()

    callback_conn = connect_db()

    client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        clean_session=False,
        userdata={"db": callback_conn},
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect

    # Reintenta hasta que el broker este disponible (arranque en frio)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as exc:
            logging.warning("MQTT broker no disponible (%s), reintento en 3s", exc)
            time.sleep(3)

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

    threading.Thread(target=heartbeat_loop, args=(client,), daemon=True).start()

    client.loop_forever()


if __name__ == "__main__":
    main()
