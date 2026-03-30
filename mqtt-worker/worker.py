import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import Json
import redis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")

MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

DB_URL = os.getenv("DATABASE_URL_WORKER") or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/postgres")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def connect_db():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    return conn


def ensure_tables(conn):
    with conn.cursor() as cur:
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
        cur.execute("UPDATE commands SET status='sent' WHERE id=%s", (command_id,))


def fetch_pending_commands(conn, limit: int = 50):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, device_id, command
            FROM commands
            WHERE status='pending'
            ORDER BY created_at ASC
            LIMIT %s;
            """,
            (limit,),
        )
        return cur.fetchall()


def handle_telemetry(conn, topic: str, payload_raw: bytes):
    try:
        data = json.loads(payload_raw.decode("utf-8"))
    except json.JSONDecodeError:
        logging.warning("Invalid JSON on %s", topic)
        return

    parts = topic.split("/")
    device_id = parts[2] if len(parts) >= 3 else None
    if not device_id:
        logging.warning("Could not parse device_id from topic %s", topic)
        return

    # Always stamp telemetry with the server time so charts and feeds show a consistent timeline.
    timestamp = datetime.now(timezone.utc)

    # Preserve device-provided ts (if any) inside the payload for debugging/reference.
    device_ts = data.get("ts")
    if device_ts is not None:
        try:
            data["device_ts"] = float(device_ts)
        except (TypeError, ValueError):
            data["device_ts"] = device_ts  # Keep original if it can't be coerced.

    upsert_device(conn, device_id, status="online", last_seen=timestamp)
    insert_telemetry(conn, device_id, timestamp, data)
    redis_client.set(f"device:{device_id}:status", "online", ex=3600)
    logging.info("Telemetry stored for %s", device_id)


def handle_status(conn, topic: str, payload_raw: bytes):
    status = payload_raw.decode("utf-8") or "unknown"
    parts = topic.split("/")
    device_id = parts[2] if len(parts) >= 3 else None
    if not device_id:
        return
    now = datetime.now(timezone.utc)
    upsert_device(conn, device_id, status=status, last_seen=now)
    redis_client.set(f"device:{device_id}:status", status, ex=3600)
    logging.info("Status %s for %s", status, device_id)


def command_dispatch_loop(conn, client: mqtt.Client):
    while True:
        try:
            pending = fetch_pending_commands(conn)
            for cmd_id, device_id, command in pending:
                # Device expects commands on /cmd (per integration doc)
                topic = f"iot/devices/{device_id}/cmd"
                # Allow raw JSON commands (dict) or simple string commands.
                try:
                    parsed = json.loads(command)
                    if isinstance(parsed, dict):
                        payload_obj = parsed
                    else:
                        payload_obj = {"cmd": command}
                except json.JSONDecodeError:
                    payload_obj = {"cmd": command}

                payload = json.dumps(payload_obj)
                result = client.publish(topic, payload)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_command_sent(conn, cmd_id)
                    logging.info("Sent command %s to %s", command, device_id)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Command dispatch error: %s", exc)
        time.sleep(5)


def on_connect(client, userdata, flags, rc):  # noqa: ANN001, D401
    if rc == 0:
        logging.info("Connected to MQTT broker")
        # Main telemetry stream expected by the dashboard
        client.subscribe("iot/devices/+/telemetry")
        # Some devices publish state instead of telemetry; ingest them the same way
        client.subscribe("iot/devices/+/state")
        client.subscribe("iot/devices/+/status")
        client.subscribe("iot/devices/+/response")
    else:
        logging.error("Failed to connect to MQTT broker: rc=%s", rc)


def on_message(client, userdata, msg):  # noqa: ANN001, D401
    conn = userdata["db"]
    if msg.topic.endswith("/telemetry") or msg.topic.endswith("/state"):
        handle_telemetry(conn, msg.topic, msg.payload)
    elif msg.topic.endswith("/status"):
        handle_status(conn, msg.topic, msg.payload)
    elif msg.topic.endswith("/response"):
        logging.info("Response from %s: %s", msg.topic, msg.payload.decode("utf-8"))


def main():
    conn = connect_db()
    ensure_tables(conn)

    client = mqtt.Client(userdata={"db": conn})
    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    dispatcher = threading.Thread(target=command_dispatch_loop, args=(conn, client), daemon=True)
    dispatcher.start()

    client.loop_forever()


if __name__ == "__main__":
    main()
