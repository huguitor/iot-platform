"""Pruebas del alta automática de equipos contra API y broker descartables.

Nunca contra producción: exige ALTA_TEST_DISPOSABLE=1 (lo pone docs/run-alta-tests.sh).
"""
import json, os, re, ssl, threading, time, unittest, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
import psycopg2

assert os.environ.get("ALTA_TEST_DISPOSABLE") == "1"
URL = os.environ["IOT_TEST_URL"]
URL_NODYN = os.environ["IOT_TEST_URL_NODYN"]
DSN = os.environ["IOT_TEST_DSN"]
FACTORY = os.environ["IOT_TEST_FACTORY"]
CA = os.environ["IOT_TEST_CA"]
PW = "Prueba-1234"
DEV = "ius-000fe9e2"     # se reclama, se libera y se vuelve a reclamar
GW = "gw-00aabbcc"       # se reprovisiona y se asigna a mano
CODE_RE = re.compile(r"^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$")


def call(method, path, token=None, body=None, headers=None, form=None, base=URL):
    data = None
    req = urllib.request.Request(base + path, method=method)
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, str(v))
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, raw.decode()


def login(user, pw=PW):
    s, b = call("POST", "/api/auth/token", form={"username": user, "password": pw})
    assert s == 200, (user, s, b)
    return b["access_token"]


def factory(device_id, token=None, reprovision=False, base=URL):
    path = "/api/factory/devices" + ("?reprovision=true" if reprovision else "")
    headers = {} if token else {"X-Factory-Token": FACTORY}
    return call("POST", path, token, {"device_id": device_id}, headers=headers, base=base)


def sql(q, args=()):
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(q, args)
        return cur.fetchall() if cur.description else None


# ------------------------------------------------------------
# MQTT
# ------------------------------------------------------------
def mqtt_connect_rc(user, pw, port=8883):
    """rc de CONNACK (0 = entró, 5 = no autorizado) o -1 si no respondió."""
    out = {"rc": -1}; ev = threading.Event()
    c = mqtt.Client(client_id="", clean_session=True)
    c.username_pw_set(user, pw)
    if port == 8883:
        c.tls_set(ca_certs=CA, tls_version=ssl.PROTOCOL_TLSv1_2)
    def on_connect(_c, _u, _f, rc):
        out["rc"] = rc; ev.set()
    c.on_connect = on_connect
    c.connect("localhost", port, 10); c.loop_start(); ev.wait(5); c.disconnect(); c.loop_stop()
    return out["rc"]


def delivered(user, pw, topic):
    """Publica como `user` en 8883 y devuelve True si el subscriptor interno lo recibe."""
    got = threading.Event(); token = uuid.uuid4().hex
    sub = mqtt.Client(client_id="test-sub-" + token[:8])
    sub.username_pw_set("worker-test", os.environ["IOT_TEST_WORKER_PW"])
    sub.on_message = lambda _c, _u, m: m.payload.decode() == token and got.set()
    subscribed = threading.Event()
    sub.on_subscribe = lambda *a: subscribed.set()
    sub.on_connect = lambda c, *a: c.subscribe(topic, qos=1)
    sub.connect("127.0.0.1", 1883, 10); sub.loop_start(); subscribed.wait(5)
    pub = mqtt.Client(client_id="")
    pub.username_pw_set(user, pw); pub.tls_set(ca_certs=CA, tls_version=ssl.PROTOCOL_TLSv1_2)
    connected = threading.Event()
    pub.on_connect = lambda *a: connected.set()
    pub.connect("localhost", 8883, 10); pub.loop_start(); connected.wait(5)
    pub.publish(topic, token, qos=1).wait_for_publish(3)
    ok = got.wait(2.5)
    for c in (pub, sub):
        c.disconnect(); c.loop_stop()
    return ok


class AltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sa = login(os.environ["API_ADMIN_USER"], os.environ["API_ADMIN_PASSWORD"])
        cls.adm1 = login("adm1"); cls.adm2 = login("adm2"); cls.u1 = login("usr1")
        s, b = call("POST", "/api/admin/companies", cls.sa, {"name": "Cliente Alta", "code": "cliente-alta"})
        assert s == 200, (s, b); cls.cb = b["id"]
        s, b = call("POST", "/api/admin/users", cls.sa, {"username": "adm-alta", "password": PW, "role": "admin", "company_id": cls.cb})
        assert s == 200, (s, b)
        cls.admb = login("adm-alta")
        s, b = call("POST", "/api/admin/users", cls.admb, {"username": "usr-alta", "password": PW, "role": "user"})
        assert s == 200, (s, b); cls.ub_id = b["id"]
        sql("""CREATE TABLE IF NOT EXISTS mqtt_events (id SERIAL PRIMARY KEY, device_id VARCHAR(255),
               direction VARCHAR(20) NOT NULL, topic VARCHAR(500) NOT NULL, payload JSONB,
               event_type VARCHAR(100) NOT NULL, processed_ok BOOLEAN NOT NULL DEFAULT TRUE,
               related_command_id INTEGER NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cls.secrets = {}

    # ---------------- fábrica ----------------
    def test_01_fabrica_exige_token_o_superadmin(self):
        s, _ = call("POST", "/api/factory/devices", body={"device_id": DEV}); self.assertEqual(s, 401)
        s, _ = call("POST", "/api/factory/devices", body={"device_id": DEV}, headers={"X-Factory-Token": "malo"}); self.assertEqual(s, 401)
        s, _ = factory(DEV, token=self.adm1); self.assertEqual(s, 403)
        for bad in ("ius-000FE9E2", "ius-123", "lab-esp8266-dell-01", "xx-000fe9e2"):
            s, _ = factory(bad); self.assertEqual(s, 422, bad)
        s, b = factory(DEV, base=URL_NODYN); self.assertEqual(s, 503, b)
        self.assertEqual(sql("select count(*) from devices where device_id=%s", (DEV,))[0][0], 0)

    def test_02_alta_de_fabrica(self):
        s, b = factory(DEV); self.assertEqual(s, 200, b)
        self.assertEqual((b["device_id"], b["model"], b["mqtt_username"]), (DEV, "ius-device", DEV))
        self.assertGreaterEqual(len(b["mqtt_password"]), 40)
        self.assertRegex(b["claim_code"], CODE_RE)
        self.assertEqual(b["claim_url"], f"https://iot.ejemplo.test/agregar?d={DEV}&c={b['claim_code'].replace('-', '')}")
        type(self).secrets[DEV] = (b["mqtt_password"], b["claim_code"])
        row = sql("select company_id, model, provisioned_at is not null, mqtt_enabled, claim_code_hash from devices where device_id=%s", (DEV,))[0]
        self.assertEqual(row[:4], (None, "ius-device", True, True))
        self.assertNotIn(b["claim_code"].replace("-", ""), row[4])
        s, _ = factory(DEV); self.assertEqual(s, 409)
        s, b2 = factory(GW, token=self.sa); self.assertEqual(s, 200, b2)
        self.assertEqual(b2["model"], "gateway-wiegand")
        type(self).secrets[GW] = (b2["mqtt_password"], b2["claim_code"])

    def test_03_sin_empresa_solo_superadmin(self):
        s, b = call("GET", f"/api/devices/{DEV}", self.sa); self.assertEqual(s, 200)
        self.assertIsNone(b["company_id"]); self.assertTrue(b["mqtt_enabled"]); self.assertEqual(b["model"], "ius-device")
        for t in (self.adm1, self.admb, self.u1):
            s, _ = call("GET", f"/api/devices/{DEV}", t); self.assertEqual(s, 404)

    def test_04_el_equipo_se_conecta_con_su_secreto(self):
        pw = self.secrets[DEV][0]
        self.assertEqual(mqtt_connect_rc(DEV, pw), 0)
        self.assertEqual(mqtt_connect_rc(DEV, "otra-clave"), 5)
        self.assertTrue(delivered(DEV, pw, f"iot/devices/{DEV}/state"))
        self.assertFalse(delivered(DEV, pw, f"iot/devices/{GW}/state"))
        self.assertFalse(delivered(DEV, pw, "iot/devices/lab-test/state"))
        self.assertTrue(delivered("lab-test", os.environ["IOT_TEST_LAB_PW"], "iot/devices/lab-test/state"))

    # ---------------- reclamo ----------------
    def test_05_reclamo_solo_admin(self):
        code = self.secrets[DEV][1]
        s, b = call("POST", "/api/devices/claim", self.sa, {"device_id": DEV, "claim_code": code}); self.assertEqual(s, 403)
        s, b = call("POST", "/api/devices/claim", self.u1, {"device_id": DEV, "claim_code": code}); self.assertEqual(s, 403)

    def test_06_reclamo_invalido_no_da_pistas(self):
        answers = set()
        for dev, code in ((DEV, "AAAA-AAAA-AAAA"), ("ius-ffffffff", self.secrets[DEV][1]), ("dev-a", "x"), (DEV, "")):
            s, b = call("POST", "/api/devices/claim", self.adm1, {"device_id": dev, "claim_code": code})
            answers.add((s, json.dumps(b)))
        self.assertEqual(answers, {(400, json.dumps({"detail": "Equipo o código inválido"}))})

    def test_07_telemetria_previa_y_reclamo(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        sql("insert into telemetry (device_id, timestamp, payload) values (%s, %s, %s)", (DEV, old, json.dumps({"previo": 1})))
        sql("insert into mqtt_events (device_id, direction, topic, event_type, created_at) values (%s,'in','t','state',%s)", (DEV, old))
        code = self.secrets[DEV][1]
        # el código se acepta en minúsculas y sin guiones (carga a mano)
        s, b = call("POST", "/api/devices/claim", self.admb, {"device_id": DEV.upper(), "claim_code": code.lower().replace("-", " ")})
        self.assertEqual(s, 200, b)
        self.assertEqual(b["company_id"], self.cb); self.assertIsNotNone(b["company_since"])
        sql("insert into telemetry (device_id, timestamp, payload) values (%s, now(), %s)", (DEV, json.dumps({"nuevo": 1})))
        sql("insert into mqtt_events (device_id, direction, topic, event_type) values (%s,'in','t','state')", (DEV,))
        s, rows = call("GET", f"/api/devices/{DEV}/telemetry", self.admb); self.assertEqual(s, 200)
        self.assertEqual([r["payload"] for r in rows], [{"nuevo": 1}])
        s, rows = call("GET", f"/api/devices/{DEV}/telemetry", self.sa); self.assertEqual(len(rows), 2)
        s, rows = call("GET", f"/api/devices/{DEV}/telemetry", self.sa, headers={"X-Company-Id": self.cb}); self.assertEqual(len(rows), 1)
        s, rows = call("GET", f"/api/admin/mqtt-events?device_id={DEV}", self.admb); self.assertEqual(s, 200); self.assertEqual(len(rows), 1)
        s, rows = call("GET", f"/api/admin/mqtt-events?device_id={DEV}", self.sa); self.assertEqual(len(rows), 2)
        # el equipo es de la empresa: lo ve su admin, no el de otra
        s, _ = call("GET", f"/api/devices/{DEV}", self.admb); self.assertEqual(s, 200)
        s, _ = call("GET", f"/api/devices/{DEV}", self.adm1); self.assertEqual(s, 404)

    def test_08_codigo_de_un_solo_uso(self):
        code = self.secrets[DEV][1]
        s, b = call("POST", "/api/devices/claim", self.adm1, {"device_id": DEV, "claim_code": code}); self.assertEqual(s, 400)
        s, b = call("POST", "/api/devices/claim", self.admb, {"device_id": DEV, "claim_code": code}); self.assertEqual(s, 400)
        self.assertIsNone(sql("select claim_code_hash from devices where device_id=%s", (DEV,))[0][0])

    def test_09_auditoria_sin_secretos(self):
        s, rows = call("GET", "/api/admin/audit?limit=200", self.admb); self.assertEqual(s, 200)
        actions = [r["action_type"] for r in rows]
        self.assertIn("device_claimed", actions)
        dump = json.dumps(sql("select payload::text, action_type from audit_logs"))
        for pw, code in self.secrets.values():
            self.assertNotIn(pw, dump); self.assertNotIn(code.replace("-", ""), dump); self.assertNotIn(code, dump)
        self.assertIn("device_provisioned", dump); self.assertIn("device_claim_failed", dump)

    def test_10_limite_de_intentos(self):
        statuses = []
        for i in range(12):
            s, _ = call("POST", "/api/devices/claim", self.adm2, {"device_id": f"ius-{i:08x}", "claim_code": "AAAA-AAAA-AAAA"})
            statuses.append(s)
        self.assertEqual(statuses[:10], [400] * 10)
        self.assertEqual(statuses[10:], [429, 429])

    # ---------------- superadmin ----------------
    def test_11_liberar(self):
        s, _ = call("PUT", f"/api/admin/users/{self.ub_id}/permissions", self.admb, {"device_id": DEV, "can_read": True, "can_write": False})
        self.assertEqual(s, 200)
        s, _ = call("POST", f"/api/admin/devices/{DEV}/release", self.admb); self.assertEqual(s, 403)
        s, b = call("POST", f"/api/admin/devices/{DEV}/release", self.sa); self.assertEqual(s, 200, b)
        self.assertRegex(b["claim_code"], CODE_RE); self.assertNotEqual(b["claim_code"], self.secrets[DEV][1])
        row = sql("select company_id, company_since from devices where device_id=%s", (DEV,))[0]
        self.assertEqual(row, (None, None))
        self.assertEqual(sql("select count(*) from user_device_permissions where device_id=%s", (DEV,))[0][0], 0)
        s, _ = call("GET", f"/api/devices/{DEV}", self.admb); self.assertEqual(s, 404)
        s, _ = call("POST", "/api/devices/claim", self.adm1, {"device_id": DEV, "claim_code": self.secrets[DEV][1]}); self.assertEqual(s, 400)
        s, d = call("POST", "/api/devices/claim", self.adm1, {"device_id": DEV, "claim_code": b["claim_code"]}); self.assertEqual(s, 200, d)
        s, rows = call("GET", f"/api/devices/{DEV}/telemetry", self.adm1); self.assertEqual(rows, [])  # nada de la empresa anterior
        s, _ = call("POST", "/api/admin/devices/dev-a/release", self.sa); self.assertEqual(s, 400)  # no es de fábrica
        self.assertEqual(mqtt_connect_rc(DEV, self.secrets[DEV][0]), 0)  # el usuario MQTT no cambia

    def test_12_deshabilitar_mqtt(self):
        pw = self.secrets[DEV][0]
        s, _ = call("PUT", f"/api/admin/devices/{DEV}/mqtt", self.adm1, {"enabled": False}); self.assertEqual(s, 403)
        s, b = call("PUT", f"/api/admin/devices/{DEV}/mqtt", self.sa, {"enabled": False}); self.assertEqual(s, 200, b)
        self.assertFalse(b["mqtt_enabled"])
        self.assertEqual(mqtt_connect_rc(DEV, pw), 5)
        s, b = call("PUT", f"/api/admin/devices/{DEV}/mqtt", self.sa, {"enabled": True}); self.assertEqual(s, 200)
        self.assertEqual(mqtt_connect_rc(DEV, pw), 0)
        s, _ = call("PUT", "/api/admin/devices/dev-a/mqtt", self.sa, {"enabled": False}); self.assertEqual(s, 400)
        s, _ = call("PUT", f"/api/admin/devices/{DEV}/mqtt", self.sa, {"enabled": False}, base=URL_NODYN); self.assertEqual(s, 503)

    def test_13_reprovisionar(self):
        old_pw, old_code = self.secrets[GW]
        s, _ = factory(DEV, reprovision=True); self.assertEqual(s, 409)        # tiene empresa
        s, _ = factory("ius-0badc0de", reprovision=True); self.assertEqual(s, 404)
        s, b = factory(GW, reprovision=True); self.assertEqual(s, 200, b)
        self.assertNotEqual(b["mqtt_password"], old_pw)
        self.assertEqual(mqtt_connect_rc(GW, old_pw), 5)
        self.assertEqual(mqtt_connect_rc(GW, b["mqtt_password"]), 0)
        s, _ = call("POST", "/api/devices/claim", self.adm1, {"device_id": GW, "claim_code": old_code}); self.assertEqual(s, 400)
        type(self).secrets[GW] = (b["mqtt_password"], b["claim_code"])

    def test_14_asignacion_manual(self):
        s, b = call("PUT", f"/api/admin/devices/{GW}", self.sa, {"set_company": True, "company_id": self.cb}); self.assertEqual(s, 200, b)
        self.assertEqual(b["company_id"], self.cb); self.assertIsNotNone(b["company_since"])
        s, _ = call("POST", "/api/devices/claim", self.adm1, {"device_id": GW, "claim_code": self.secrets[GW][1]}); self.assertEqual(s, 400)
        s, b = call("PUT", f"/api/admin/devices/{GW}", self.sa, {"set_company": True, "company_id": None}); self.assertEqual(s, 200)
        self.assertIsNone(b["company_since"])
        # sin empresa pero sin código: hace falta liberarlo para tener etiqueta nueva
        s, _ = call("POST", "/api/devices/claim", self.adm1, {"device_id": GW, "claim_code": self.secrets[GW][1]}); self.assertEqual(s, 400)

    def test_15_equipos_viejos_intactos(self):
        row = sql("select company_id is not null, company_since, provisioned_at, mqtt_enabled from devices where device_id='dev-a'")[0]
        self.assertEqual(row, (True, None, None, None))
        s, rows = call("GET", "/api/devices/dev-a/telemetry", self.adm1); self.assertEqual(s, 200)
        # equipo viejo asignado a mano por las pruebas de empresas: también marca company_since
        self.assertIsNotNone(sql("select company_since from devices where device_id='worker-nuevo'")[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
