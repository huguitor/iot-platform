"""No MQTT connections/publications: callbacks use mocks; DB tests use tmpfs PG."""

from datetime import datetime, timedelta, timezone
import json
import os
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import worker


class WorkerUnitTests(unittest.TestCase):
    def tearDown(self):
        worker.heartbeat_ready.clear()
        worker.event_sub_mid["mid"] = None

    def test_topic_identity_is_strict(self):
        self.assertEqual(worker.parse_device_id_from_topic("iot/devices/taller-01/event"), "taller-01")
        for topic in ("other/devices/test/event", "iot/devices/test/extra/event", "iot/devices/+/event"):
            self.assertIsNone(worker.parse_device_id_from_topic(topic))

    def test_invalid_and_finite_delays(self):
        now = datetime.now(timezone.utc)
        for delay in (True, "3", -1, float("nan"), float("inf"), 30 * 86400 + 1):
            occurred, valid, _ = worker.event_timing({"delay_s": delay}, now)
            self.assertFalse(valid)
            self.assertEqual(occurred, now)
        self.assertEqual(worker.event_timing({"delay_s": 3}, now), (now - timedelta(seconds=3), True, 3))

    def test_heartbeat_requires_successful_matching_suback(self):
        worker.event_sub_mid["mid"] = 7
        for mid, granted in ((8, [1]), (7, [128]), (7, [0]), (7, [])):
            worker.on_subscribe(None, None, mid, granted)
            self.assertFalse(worker.heartbeat_ready.is_set())
        worker.on_subscribe(None, None, 7, [1])
        self.assertTrue(worker.heartbeat_ready.is_set())
        worker.on_disconnect(None, None, 1)
        self.assertFalse(worker.heartbeat_ready.is_set())

    def test_connect_subscribes_qos1_and_resets_readiness(self):
        client = MagicMock()
        client.subscribe.return_value = (0, 12)
        worker.heartbeat_ready.set()
        worker.on_connect(client, None, {}, 0)
        client.subscribe.assert_any_call("iot/devices/+/event", qos=1)
        client.subscribe.assert_any_call("iot/devices/+/info")
        self.assertFalse(worker.heartbeat_ready.is_set())
        self.assertEqual(worker.event_sub_mid["mid"], 12)
        worker.on_connect(client, None, {}, 5)
        self.assertIsNone(worker.event_sub_mid["mid"])

    def test_db_failure_propagates_before_paho_ack(self):
        msg = SimpleNamespace(topic="iot/devices/taller-01/event", payload=b"{}", retain=False)
        with patch.object(worker, "ensure_connection", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                worker.on_message(MagicMock(), {}, msg)

    def test_paho_does_not_ack_a_failed_callback(self):
        client = worker.mqtt.Client()
        topic = b"iot/devices/taller-01/event"
        packet = struct.pack("!H", len(topic)) + topic + struct.pack("!H", 7) + b"{}"
        client._in_packet = {"command": worker.mqtt.PUBLISH | 2, "packet": packet}
        def fail(*args):
            raise RuntimeError("fixture database failure")
        client.on_message = fail
        with patch.object(client, "_send_puback") as ack:
            with self.assertRaises(RuntimeError):
                client._handle_publish()
            ack.assert_not_called()
            client.on_message = lambda *args: None
            client._handle_publish()
            ack.assert_called_once_with(7)

    def test_heartbeat_is_mocked_and_not_retained(self):
        client = MagicMock()
        self.assertFalse(worker.send_heartbeat_if_ready(client))
        client.publish.assert_not_called()
        worker.heartbeat_ready.set()
        client.publish.return_value.rc = 0
        self.assertTrue(worker.send_heartbeat_if_ready(client))
        args, kwargs = client.publish.call_args
        self.assertEqual(args[0], worker.HEARTBEAT_TOPIC)
        self.assertEqual(kwargs, {"qos": 0, "retain": False})

    def test_initial_connect_retries_and_uses_persistent_session(self):
        client = MagicMock()
        client.connect.side_effect = [OSError("fixture offline"), 0]
        with patch.object(worker, "connect_db"), patch.object(worker, "validate_firmware_schema"), \
                patch.object(worker, "ensure_tables"), patch.object(worker.mqtt, "Client", return_value=client) as factory, \
                patch.object(worker.threading, "Thread"), patch.object(worker.time, "sleep") as sleep:
            worker.main()
        self.assertFalse(factory.call_args.kwargs["clean_session"])
        self.assertEqual(factory.call_args.kwargs["client_id"], worker.MQTT_CLIENT_ID)
        self.assertEqual(client.connect.call_count, 2)
        client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)
        sleep.assert_called_once_with(3)

    def test_nonfinite_json_is_rejected_for_raw_evidence(self):
        for value in (b'{"delay_s":NaN}', b'{"value":1e999}'):
            self.assertIsNone(worker.safe_json_loads(value))


@unittest.skipUnless(os.getenv("IOT_MIGRATION_TEST_DISPOSABLE") == "1", "requires isolated runner")
class WorkerDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sqlalchemy as sa
        from app.database import Base
        from app import models  # noqa: F401
        from app.access_control import models as access_models  # noqa: F401

        if os.environ.get("DATABASE_URL_WORKER") != "postgresql://postgres@127.0.0.1:5432/iot_firmware_migration_test":
            raise RuntimeError("Refusing to reset any database outside the isolated runner")
        cls.engine = sa.create_engine(os.environ["DATABASE_URL"])
        cls.base = Base

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE")
            c.exec_driver_sql("CREATE SCHEMA public")
        self.base.metadata.create_all(self.engine)
        self.conn = worker.connect_db()
        # mqtt_events is currently maintained by the legacy worker bootstrap,
        # rather than API metadata. Exercise it only in the disposable database.
        worker.ensure_tables(self.conn)
        self.redis = patch.object(worker, "redis_client", MagicMock())
        self.redis.start()

    def tearDown(self):
        self.redis.stop()
        self.conn.close()

    def query(self, statement, params=()):
        with self.conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall()

    def event(self, data, retained=False, conn=None):
        worker.handle_event(conn or self.conn, "iot/devices/taller-01/event",
                            json.dumps(data).encode(), retained=retained)

    def test_firmware_info_and_boot_merge_without_false_presence(self):
        with self.conn:
            worker.handle_info(self.conn, "iot/devices/taller-01/info",
                               b'{"fw":"1.0","gateway_fw":"2.0","cap":64}', retained=True)
        self.assertEqual(self.query("SELECT status,last_seen,fw_version,gateway_fw_version,card_capacity FROM devices"),
                         [("offline", None, "1.0", "2.0", 64)])
        self.event({"event": "boot", "fw": "1.1"})
        self.assertEqual(self.query("SELECT fw_version,gateway_fw_version,card_capacity FROM devices"), [("1.1", "2.0", 64)])
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM mqtt_events"), [(2,)])
        self.assertEqual(self.query("SELECT count(*) FROM audit_logs"), [(2,)])
        with self.conn:
            worker.handle_info(self.conn, "iot/devices/taller-01/info", b'{"fw":"old"}', retained=True)
        self.assertEqual(self.query("SELECT fw_version FROM devices"), [("1.1",)])

    def test_delayed_events_preserve_current_state_and_original_history(self):
        self.event({"event": "access", "door_opened": True, "reader": "new"})
        self.event({"event": "access", "door_opened": False, "reader": "old", "delay_s": 120})
        self.event({"event": "door_state", "door_sensor": "closed", "reader": "older", "delay_s": 180})
        self.assertEqual(self.query("SELECT last_door_opened,last_door_reader,last_door_sensor_state FROM devices"),
                         [(True, "new", "closed")])
        rows = self.query("SELECT created_at,payload FROM access_events ORDER BY id")
        self.assertEqual(len(rows), 3)
        self.assertLess(rows[1][0], rows[0][0])
        self.assertEqual(rows[1][1]["delay_s"], 120)

    def test_duplicate_identity_survives_connection_changes_and_keeps_receipts(self):
        data = {"event": "access", "door_opened": True, "boot_id": "boot-A", "event_id": 7}
        self.event(data)
        other = worker.connect_db()
        try:
            self.event({**data, "delay_s": 10}, conn=other)
        finally:
            other.close()
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(1,)])
        self.assertEqual(self.query("SELECT count(*) FROM mqtt_events"), [(2,)])
        self.assertEqual(self.query("SELECT count(*) FROM audit_logs"), [(2,)])
        self.assertEqual(self.query("SELECT event_type FROM mqtt_events ORDER BY id DESC LIMIT 1"),
                         [("mqtt_duplicate_event_received",)])

    def test_no_identity_does_not_drop_two_real_equal_events(self):
        for _ in range(2):
            self.event({"event": "access", "reader": "L1"})
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(2,)])

    def test_conflicting_identity_is_recorded_without_replacing_original(self):
        data = {"event": "access", "door_opened": True, "boot_id": "A", "event_id": "1"}
        self.event(data)
        self.event({**data, "door_opened": False})
        self.assertEqual(self.query("SELECT last_door_opened FROM devices"), [(True,)])
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(1,)])
        self.assertEqual(self.query("SELECT event_type,processed_ok FROM mqtt_events ORDER BY id DESC LIMIT 1"),
                         [("mqtt_event_identity_conflict", False)])

    def test_transaction_rolls_back_domain_and_receipts_together(self):
        with patch.object(worker, "insert_audit_log", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                self.event({"event": "access", "door_opened": True})
        for table in ("devices", "access_events", "mqtt_events"):
            self.assertEqual(self.query("SELECT count(*) FROM " + table), [(0,)])
        worker.redis_client.set.assert_not_called()

    def test_concurrent_duplicates_have_only_one_domain_effect(self):
        errors = []
        barrier = threading.Barrier(2)
        def receive():
            conn = worker.connect_db()
            try:
                barrier.wait(timeout=5)
                self.event({"event": "access", "boot_id": "A", "event_id": 1}, conn=conn)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                conn.close()
        threads = [threading.Thread(target=receive) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(1,)])
        self.assertEqual(self.query("SELECT count(*) FROM mqtt_events"), [(2,)])

    def test_retained_and_invalid_delay_do_not_update_live_state(self):
        self.event({"event": "access", "door_opened": True, "delay_s": "bad"})
        self.event({"event": "access", "door_opened": True}, retained=True)
        self.assertEqual(self.query("SELECT last_seen,last_door_opened FROM devices"), [(None, None)])
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(2,)])
        self.assertEqual(self.query("SELECT count(*) FROM audit_logs WHERE result='invalid_event_delay'"), [(1,)])

    def test_technical_events_remain_auditable_without_access_history(self):
        for event in ("list", "stats", "stats_reset", "boot"):
            self.event({"event": event})
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM mqtt_events"), [(4,)])
        self.assertEqual(self.query("SELECT count(*) FROM audit_logs"), [(4,)])

    def test_invalid_fields_and_nonfinite_payload_keep_diagnostics(self):
        self.event({"event": ["invalid"]})
        self.event({"event": "access", "reader": "X" * 51})
        worker.handle_event(self.conn, "iot/devices/taller-01/event", b'{"event":"access","delay_s":NaN}')
        self.assertEqual(self.query("SELECT count(*) FROM access_events"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM mqtt_events WHERE NOT processed_ok"), [(3,)])
        self.assertEqual(self.query("SELECT count(*) FROM audit_logs"), [(3,)])

    def test_orm_and_api_schema_expose_firmware(self):
        from sqlalchemy.orm import Session
        from app.models import Device
        from app.schemas import Device as DeviceSchema

        with self.conn:
            worker.handle_info(self.conn, "iot/devices/taller-01/info", b'{"fw":"test-fw","cap":32}')
        with Session(self.engine) as session:
            result = DeviceSchema.from_orm(session.query(Device).one())
            self.assertEqual(result.fw_version, "test-fw")
            self.assertEqual(result.card_capacity, 32)
            self.assertIsNotNone(result.info_at)


if __name__ == "__main__":
    unittest.main()
