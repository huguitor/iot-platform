"""Prueba de la API de integración contra una API real con PostgreSQL descartable.

Uso (ver docs/integracion-control-acceso.md):
  IOT_TEST_URL=http://127.0.0.1:PUERTO IOT_TEST_KEY=clave IOT_TEST_DSN=postgresql://... python3 live_access_integration.py
No usar contra producción: inserta dispositivos y eventos de prueba.
"""
import json
import os
import unittest
import urllib.error
import urllib.request

import psycopg2

URL, KEY, DSN = os.environ["IOT_TEST_URL"], os.environ["IOT_TEST_KEY"], os.environ["IOT_TEST_DSN"]
BASE = URL + "/api/integrations/access"


def call(method, path, body=None, key=KEY):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


class AccessIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = psycopg2.connect(DSN)
        cls.db.autocommit = True
        cur = cls.db.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS mqtt_events (
            id SERIAL PRIMARY KEY, device_id VARCHAR(255), direction VARCHAR(20) NOT NULL,
            topic VARCHAR(500) NOT NULL, payload JSONB, event_type VARCHAR(100) NOT NULL,
            processed_ok BOOLEAN NOT NULL DEFAULT TRUE, related_command_id INTEGER NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cur.execute("INSERT INTO devices (device_id, status) VALUES ('gw-a','online'), ('gw-b','offline') ON CONFLICT DO NOTHING")

    def insert(self, device, payload, event_type="mqtt_device_event_received", topic=None, direction="inbound"):
        cur = self.db.cursor()
        cur.execute(
            "INSERT INTO mqtt_events (device_id, direction, topic, payload, event_type) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (device, direction, topic or f"iot/devices/{device}/event", json.dumps(payload), event_type),
        )
        return cur.fetchone()[0]

    def test_auth(self):
        self.assertEqual(call("GET", "/devices", key=None)[0], 401)
        self.assertEqual(call("GET", "/devices", key="otra")[0], 401)
        self.assertEqual(call("GET", "/devices")[0], 200)

    def test_devices(self):
        code, data = call("GET", "/devices?device_ids=gw-a")
        self.assertEqual((code, [d["device_id"] for d in data], data[0]["status"]), (200, ["gw-a"], "online"))

    def test_events_cursor_filters_and_delay(self):
        start = call("GET", "/events/cursor")[1]["last_id"]
        a1 = self.insert("gw-a", {"event": "access", "reader": "L1", "card_uid": "123", "authorized": True}, "access_event_received")
        self.insert("gw-a", {"event": "telemetry"}, "mqtt_telemetry_received", topic="iot/devices/gw-a/telemetry")
        self.insert("gw-a", {"event": "access"}, "mqtt_event_duplicate")
        self.insert("gw-a", {"cmd": "enroll"}, "mqtt_command_dispatched", topic="iot/devices/gw-a/cmd", direction="outbound")
        b1 = self.insert("gw-b", {"event": "door", "reader": "L1", "state": "OPEN", "delay_s": 60})
        a2 = self.insert("gw-a", {"event": "enroll", "reader": "L1", "card_uid": "123", "result": "OK"})

        code, data = call("GET", f"/events?after_id={start}")
        self.assertEqual(code, 200)
        self.assertEqual([e["id"] for e in data["events"]], [a1, b1, a2])
        door = data["events"][1]
        self.assertEqual(door["event"], "door")
        self.assertNotEqual(door["occurred_at"], door["received_at"])
        self.assertEqual(data["next_after_id"], a2)

        data = call("GET", f"/events?after_id={start}&device_ids=gw-a&limit=1")[1]
        self.assertEqual(([e["id"] for e in data["events"]], data["more"]), ([a1], True))
        data = call("GET", f"/events?after_id={a2}")[1]
        self.assertEqual((data["events"], data["next_after_id"]), ([], a2))

    def test_commands(self):
        code, data = call("POST", "/devices/gw-a/commands", {"cmd": "enroll", "card_uid": "005033541"})
        self.assertEqual((code, data["command"]), (201, {"cmd": "enroll", "card_uid": "5033541"}))
        cur = self.db.cursor()
        cur.execute("SELECT command, status FROM commands WHERE id=%s", (data["id"],))
        command, st = cur.fetchone()
        self.assertEqual((json.loads(command), st), ({"cmd": "enroll", "card_uid": "5033541"}, "pending"))
        self.assertEqual(call("GET", f"/commands/{data['id']}")[1]["status"], "pending")

        self.assertEqual(call("POST", "/devices/gw-a/commands", {"cmd": "delete", "card_uid": "16777215"})[0], 422)
        self.assertEqual(call("POST", "/devices/gw-a/commands", {"cmd": "enroll", "card_uid": "12a"})[0], 422)
        self.assertEqual(call("POST", "/devices/gw-a/commands", {"cmd": "reset"})[0], 422)
        self.assertEqual(call("POST", "/devices/gw-a/commands", {"cmd": "output_on"})[0], 422)
        self.assertEqual(call("POST", "/devices/nada/commands", {"cmd": "list"})[0], 404)
        code, data = call("POST", "/devices/gw-a/commands", {"cmd": "list", "start": 50, "count": 50})
        self.assertEqual(data["command"], {"cmd": "list", "start": 50, "count": 50})
        cur.execute("SELECT count(*) FROM audit_logs WHERE action_type='integration_command_requested'")
        self.assertGreaterEqual(cur.fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
