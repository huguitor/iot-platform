"""MQTT 5 / TLS / WebSocket checks in a network-none disposable broker only."""

import base64
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import sys
import unittest
import uuid
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
SECRETS = {}
DOMAIN = "iot.panozoelectronica.com.ar"
DEVICE = "lab-esp8266-dell-20261104-01"
PUBLIC_USER = "lab-esp8266-dell-01"
ROOT = "iot/devices/" + DEVICE


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
# The isolated runner mounts the same tools at /tools.
if Path('/tools').exists():
    sys.path.insert(0, '/tools')
from mqtt_probe_client import MQTT


@unittest.skipUnless(os.getenv("IOT_TLS_DISPOSABLE") == "1", "requires disposable broker runner")
class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        routes = Path("/proc/net/route").read_text().splitlines()[1:]
        if any(line.split()[0] != "lo" for line in routes):
            raise RuntimeError("Refusing MQTT tests outside the isolated network namespace")

    def setUp(self):
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()

    def client(self, port=8883, public=True, anonymous=False, websocket=False, **kwargs):
        user = PUBLIC_USER if public else SECRETS["internal_username"]
        password = SECRETS["public_password"] if public else SECRETS["internal_password"]
        client = MQTT(port, None if anonymous else user, None if anonymous else password,
                      secure=port == 8883, websocket=websocket, **kwargs)
        self.clients.append(client)
        return client

    def test_public_user_only_works_on_tls(self):
        self.assertEqual(self.client().reason, 0)
        self.assertNotEqual(self.client(port=1883).reason, 0)
        self.assertNotEqual(self.client(port=9001, websocket=True).reason, 0)

    def test_actual_internal_credentials_rejected_on_tls(self):
        self.assertEqual(self.client(port=1883, public=False).reason, 0)
        self.assertEqual(self.client(port=9001, public=False, websocket=True).reason, 0)
        self.assertNotEqual(self.client(public=False).reason, 0)

    def test_anonymous_rejected_everywhere(self):
        for port in (1883, 9001, 8883):
            with self.subTest(port=port):
                self.assertNotEqual(self.client(port=port, anonymous=True, websocket=port == 9001).reason, 0)

    def test_wrong_password_rejected(self):
        c = MQTT(8883, PUBLIC_USER, "intentionally-invalid-fixture", secure=True)
        self.clients.append(c)
        self.assertNotEqual(c.reason, 0)

    def test_tls_12_and_13_work(self):
        for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            self.assertEqual(self.client(version=version).reason, 0)

    def test_tls_11_rejected(self):
        with self.assertRaises(ssl.SSLError) as failure:
            self.client(version=ssl.TLSVersion.TLSv1_1)
        self.assertIn(failure.exception.reason, ("TLSV1_ALERT_PROTOCOL_VERSION", "UNSUPPORTED_PROTOCOL"))

    def test_wrong_hostname_rejected(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.client(hostname="wrong.invalid")

    def test_unknown_ca_rejected(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.client(trusted=False)

    def test_public_write_allowlist_delivered_to_internal_observer(self):
        observer = self.client(port=1883, public=False)
        self.assertLess(observer.subscribe(ROOT + "/#"), 128)
        publisher = self.client()
        for suffix in ("event", "info", "status", "telemetry"):
            topic = ROOT + "/" + suffix
            self.assertEqual(publisher.publish(topic), 0)
            self.assertEqual(observer.receive(), (topic, b"isolated-tls-fixture"))

    def test_public_cannot_publish_commands_heartbeat_or_other_device(self):
        observer = self.client(port=1883, public=False)
        for topic in (ROOT + "/cmd", "iot/platform/worker/heartbeat", "iot/devices/other-lab/event"):
            observer.subscribe(topic)
        publisher = self.client()
        for topic in (ROOT + "/cmd", "iot/platform/worker/heartbeat", "iot/devices/other-lab/event"):
            self.assertGreaterEqual(publisher.publish(topic), 128)
            self.assertIsNone(observer.receive())

    def test_public_read_allowlist_only(self):
        internal = self.client(port=1883, public=False)
        subscriber = self.client()
        for topic in (ROOT + "/cmd", "iot/platform/worker/heartbeat"):
            self.assertLess(subscriber.subscribe(topic), 128)
            self.assertEqual(internal.publish(topic), 0)
            self.assertEqual(subscriber.receive(), (topic, b"isolated-tls-fixture"))
        for topic in (ROOT + "/event", "iot/devices/other-lab/cmd"):
            subscriber.subscribe(topic)
            self.assertEqual(internal.publish(topic), 0)
            self.assertIsNone(subscriber.receive())

    def test_tls_reconnect_authenticates_again(self):
        for _ in range(3):
            self.assertEqual(self.client().reason, 0)

    def test_mqtt_311_used_by_esp8266_has_the_same_auth_isolation(self):
        self.assertEqual(self.client(mqtt_level=4).reason, 0)
        self.assertNotEqual(self.client(public=False, mqtt_level=4).reason, 0)
        self.assertEqual(self.client(port=1883, public=False, mqtt_level=4).reason, 0)
        self.assertEqual(self.client(port=9001, public=False, websocket=True, mqtt_level=4).reason, 0)
        self.assertNotEqual(self.client(port=1883, mqtt_level=4).reason, 0)

    def test_public_client_cannot_disconnect_internal_client_by_id_collision(self):
        internal = self.client(port=1883, public=False, client_id="internal-id-fixture")
        self.assertEqual(internal.reason, 0)
        self.assertEqual(self.client(client_id="internal-id-fixture").reason, 0)
        internal.send(0xC0)
        self.assertEqual(internal.packet(), (0xD0, b""))


if __name__ == "__main__":
    if os.getenv("IOT_TLS_DISPOSABLE") != "1":
        raise SystemExit("Use docs/test-mqtt-tls.py; never run against an active broker")
    SECRETS.update(json.load(sys.stdin))
    unittest.main(verbosity=2)
