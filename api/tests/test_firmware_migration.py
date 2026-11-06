"""Integration tests; enabled only inside the disposable PostgreSQL runner."""

import os
from pathlib import Path
import unittest


@unittest.skipUnless(
    os.getenv("IOT_MIGRATION_TEST_DISPOSABLE") == "1",
    "requires docs/run-firmware-migration-tests.py (disposable database)",
)
class FirmwareMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sqlalchemy as sa
        from alembic.config import Config

        url = os.environ.get("DATABASE_URL", "")
        if url != "postgresql+psycopg2://postgres@127.0.0.1:5432/iot_firmware_migration_test":
            raise RuntimeError("Refusing to reset a database outside the disposable runner")
        cls.sa = sa
        cls.engine = sa.create_engine(url)
        cls.config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cls.config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
        )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from alembic import command

        with self.engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE")
            c.exec_driver_sql("CREATE SCHEMA public")
            c.exec_driver_sql("CREATE TABLE devices (id serial PRIMARY KEY, device_id varchar(120) UNIQUE NOT NULL)")
            c.exec_driver_sql("INSERT INTO devices (device_id) VALUES ('fixture-arduino')")
            c.exec_driver_sql("CREATE TABLE fixture_users (id integer PRIMARY KEY)")
            c.exec_driver_sql("INSERT INTO fixture_users VALUES (7)")
            c.exec_driver_sql("CREATE TABLE fixture_permissions (user_id integer REFERENCES fixture_users(id), device_id varchar(120) REFERENCES devices(device_id))")
            c.exec_driver_sql("INSERT INTO fixture_permissions VALUES (7, 'fixture-arduino')")
        command.stamp(self.config, "20260818_01")

    def upgrade(self):
        from alembic import command

        command.upgrade(self.config, "20261104_01")

    def columns(self):
        return {c["name"]: c for c in self.sa.inspect(self.engine).get_columns("devices")}

    def version(self):
        with self.engine.connect() as c:
            return c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    def test_vps_missing_columns_preserves_rows_and_relations(self):
        self.upgrade()
        columns = self.columns()
        for name in ("fw_version", "gateway_fw_version", "card_capacity", "info", "info_at"):
            self.assertTrue(columns[name]["nullable"])
            self.assertIsNone(columns[name]["default"])
        self.assertEqual(columns["fw_version"]["type"].length, 50)
        self.assertEqual(columns["gateway_fw_version"]["type"].length, 50)
        self.assertEqual(str(columns["card_capacity"]["type"]), "INTEGER")
        self.assertEqual(str(columns["info"]["type"]), "JSONB")
        self.assertTrue(columns["info_at"]["type"].timezone)
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql("SELECT * FROM fixture_permissions").all(), [(7, "fixture-arduino")])
            self.assertEqual(c.exec_driver_sql("SELECT id, device_id, fw_version, gateway_fw_version, card_capacity, info, info_at FROM devices").all(), [(1, "fixture-arduino", None, None, None, None, None)])
        self.assertEqual(self.version(), "20261104_01")

    def test_debian_existing_columns_and_values_are_preserved(self):
        with self.engine.begin() as c:
            c.exec_driver_sql("ALTER TABLE devices ADD fw_version varchar(50), ADD gateway_fw_version varchar(50), ADD card_capacity integer, ADD info jsonb, ADD info_at timestamptz")
            c.exec_driver_sql("UPDATE devices SET fw_version='fixture-fw', gateway_fw_version='fixture-gw', card_capacity=10, info='{\"fixture\":true}', info_at='2026-11-04 12:00:00+00'")
        with self.engine.connect() as c:
            before = c.exec_driver_sql("SELECT * FROM devices").all()
        self.upgrade()
        self.upgrade()
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql("SELECT * FROM devices").all(), before)
        self.assertEqual(self.version(), "20261104_01")

    def test_partial_schema(self):
        with self.engine.begin() as c:
            c.exec_driver_sql("ALTER TABLE devices ADD info jsonb")
            c.exec_driver_sql("UPDATE devices SET info='{\"fixture\":true}'")
        self.upgrade()
        self.assertEqual(len(self.columns()), 7)
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql("SELECT info FROM devices").scalar_one(), {"fixture": True})

    def test_incompatible_definitions_abort_before_adding_columns(self):
        definitions = [
            "varchar(49)", "text", "integer", "varchar(50) NOT NULL DEFAULT 'fixture'",
            "varchar(50) DEFAULT 'fixture'",
        ]
        for definition in definitions:
            with self.subTest(definition=definition):
                with self.engine.begin() as c:
                    c.exec_driver_sql("ALTER TABLE devices ADD fw_version " + definition)
                with self.assertRaisesRegex(RuntimeError, "Incompatible"):
                    self.upgrade()
                self.assertEqual(set(self.columns()), {"id", "device_id", "fw_version"})
                self.assertEqual(self.version(), "20260818_01")
                with self.engine.begin() as c:
                    c.exec_driver_sql("ALTER TABLE devices DROP fw_version")

    def test_late_incompatibility_does_not_add_earlier_columns(self):
        with self.engine.begin() as c:
            c.exec_driver_sql("ALTER TABLE devices ADD info_at timestamp without time zone")
        with self.assertRaisesRegex(RuntimeError, "Incompatible"):
            self.upgrade()
        self.assertEqual(set(self.columns()), {"id", "device_id", "info_at"})
        self.assertEqual(self.version(), "20260818_01")

    def test_downgrade_refuses_to_delete_firmware_data(self):
        from alembic import command

        self.upgrade()
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE devices SET fw_version='keep-me'")
        with self.assertRaisesRegex(RuntimeError, "downgrade is disabled"):
            command.downgrade(self.config, "20260818_01")
        self.assertEqual(self.version(), "20261104_01")
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql("SELECT fw_version FROM devices").scalar_one(), "keep-me")


if __name__ == "__main__":
    unittest.main()
