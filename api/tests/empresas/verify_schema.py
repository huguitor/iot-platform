import os, psycopg2
assert os.environ.get("EMPRESAS_TEST_DISPOSABLE") == "1"
c = psycopg2.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
def q(sql): cur.execute(sql); return cur.fetchall()
checks = {
 "alembic": q("select version_num from alembic_version"),
 "companies": q("select id, name, code from companies"),
 "devices_sin_empresa": q("select count(*) from devices where company_id is null"),
 "users": q("select role, company_id is null, count(*) from users group by 1,2 order by 1"),
 "audit_sin_empresa": q("select count(*) from audit_logs where company_id is null"),
 "events_sin_empresa": q("select count(*) from access_events where company_id is null"),
 "access_tables_intactas": q("select count(*) from information_schema.columns where table_name like 'access\\_%' and table_name <> 'access_events' and column_name='company_id'"),
}
for k, v in checks.items(): print(k, v)
assert checks["alembic"] == [(os.environ.get("EXPECTED_ALEMBIC", "20261107_01"),)]
assert len(checks["companies"]) == 1 and checks["companies"][0][2] == "panozo"
assert checks["devices_sin_empresa"] == [(0,)]
assert ("superadmin", True, 1) in checks["users"] and ("admin", False, 2) in checks["users"] and ("user", False, 4) in checks["users"]
assert checks["audit_sin_empresa"] == [(0,)] and checks["events_sin_empresa"] == [(0,)]
assert checks["access_tables_intactas"] == [(0,)]
# trigger: equipo nuevo del worker sin empresa; evento de un equipo con empresa
cur.execute("insert into devices (device_id, status) values ('worker-nuevo','online')")
cur.execute("insert into access_events (device_id, reader, event_type) values ('dev-b','L1','access') returning company_id")
assert cur.fetchone()[0] is not None
cur.execute("insert into audit_logs (device_id, action_type, result) values ('worker-nuevo','mqtt_state_received','success') returning company_id")
assert cur.fetchone()[0] is None
c.commit()
print("SCHEMA OK")
