"""Pruebas HTTP de empresas contra una API descartable. Nunca contra producción."""
import json, os, unittest, urllib.error, urllib.parse, urllib.request
assert os.environ.get("EMPRESAS_TEST_DISPOSABLE") == "1"
URL = os.environ["IOT_TEST_URL"]
PW = "Prueba-1234"

def call(method, path, token=None, body=None, headers=None, form=None):
    data = None
    req = urllib.request.Request(URL + path, method=method)
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items(): req.add_header(k, str(v))
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw or b"null")
        except ValueError: return e.code, raw.decode()

def login(user, pw=PW):
    s, b = call("POST", "/api/auth/token", form={"username": user, "password": pw})
    return b["access_token"] if s == 200 else None

def ids(devs): return sorted(d["device_id"] for d in devs)

class EmpresasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sa = login(os.environ["API_ADMIN_USER"], os.environ["API_ADMIN_PASSWORD"])
        cls.adm1 = login("adm1"); cls.u1 = login("usr1")
        assert cls.sa and cls.adm1 and cls.u1
        s, b = call("POST", "/api/admin/companies", cls.sa, {"name": "Cliente B", "code": "cliente-b"})
        assert s == 200, (s, b); cls.cb = b["id"]
        s, b = call("GET", "/api/admin/companies", cls.sa); cls.panozo = [c for c in b if c["code"] == "panozo"][0]["id"]
        s, b = call("POST", "/api/admin/users", cls.sa, {"username": "admb", "password": PW, "role": "admin", "company_id": cls.cb})
        assert s == 200, (s, b)
        cls.admb = login("admb")
        s, b = call("POST", "/api/admin/users", cls.admb, {"username": "usr-b", "password": PW, "role": "user"})
        assert s == 200 and b["company_id"] == cls.cb, (s, b)
        cls.ub_id = b["id"]

    def test_01_superadmin_ve_todo_y_filtra(self):
        s, b = call("GET", "/api/devices", self.sa); self.assertEqual(s, 200)
        self.assertIn("worker-nuevo", ids(b))  # sin empresa: solo superadmin
        s, b = call("GET", "/api/devices", self.sa, headers={"X-Company-Id": self.cb})
        self.assertEqual(ids(b), [])
        s, b = call("GET", "/api/devices", self.sa, headers={"X-Company-Id": self.panozo})
        self.assertEqual(ids(b), ["dev-a", "dev-b", "lab-test"])

    def test_02_admin_ve_solo_su_empresa(self):
        s, b = call("GET", "/api/devices", self.adm1)
        self.assertEqual(ids(b), ["dev-a", "dev-b", "lab-test"])
        s, b = call("GET", "/api/devices", self.adm1, headers={"X-Company-Id": self.cb})  # se ignora
        self.assertEqual(ids(b), ["dev-a", "dev-b", "lab-test"])
        s, b = call("GET", "/api/devices", self.admb); self.assertEqual(ids(b), [])
        self.assertEqual(call("GET", "/api/devices/worker-nuevo", self.adm1)[0], 404)

    def test_03_user_con_permiso(self):
        s, b = call("GET", "/api/devices", self.u1); self.assertEqual(ids(b), ["dev-a"])

    def test_04_asignar_equipo(self):
        s, b = call("PUT", "/api/admin/devices/worker-nuevo", self.admb, {"set_company": True, "company_id": self.cb})
        self.assertIn(s, (403, 404))  # admin no asigna empresas
        s, b = call("PUT", "/api/admin/devices/worker-nuevo", self.sa, {"set_company": True, "company_id": self.cb, "name": "Equipo B"})
        self.assertEqual(s, 200, b); self.assertEqual(b["company_id"], self.cb)
        s, b = call("GET", "/api/devices", self.admb); self.assertEqual(ids(b), ["worker-nuevo"])
        self.assertEqual(call("GET", "/api/devices/worker-nuevo", self.admb)[0], 200)
        self.assertEqual(call("GET", "/api/devices/worker-nuevo", self.adm1)[0], 404)
        s, b = call("PUT", "/api/admin/devices/worker-nuevo", self.admb, {"name": "Renombrado"})
        self.assertEqual(s, 200); self.assertEqual(b["name"], "Renombrado")
        self.assertEqual(call("PUT", "/api/admin/devices/dev-a", self.admb, {"name": "X"})[0], 404)

    def test_05_comandos_por_empresa(self):
        self.assertEqual(call("POST", "/api/devices/dev-a/command", self.admb, {"command": '{"cmd":"get_state"}'})[0], 404)
        self.assertEqual(call("POST", "/api/devices/no-existe/command", self.adm1, {"command": "x"})[0], 404)
        self.assertEqual(call("POST", "/api/devices/dev-a/command", self.adm1, {"command": '{"cmd":"get_state"}'})[0], 200)

    def test_06_usuarios_por_empresa(self):
        s, b = call("GET", "/api/admin/users", self.adm1)
        names = sorted(u["username"] for u in b)
        self.assertEqual(names, ["usr1", "usr2", "usr3", "usr4"])  # todos los user de Panozo, no solo los creados por adm1
        s, b = call("GET", "/api/admin/users", self.admb)
        self.assertEqual(sorted(u["username"] for u in b), ["usr-b"])
        # admin de Panozo no puede dar permisos a un usuario de otra empresa
        s, b = call("PUT", f"/api/admin/users/{self.ub_id}/permissions", self.adm1, {"device_id": "dev-a", "can_read": True})
        self.assertIn(s, (400, 403, 404), b)

    def test_07_auditoria_y_eventos(self):
        s, b = call("GET", "/api/admin/audit", self.admb); self.assertEqual(s, 200)
        self.assertTrue(all(r.get("username") in (None, "admb", "usr-b", os.environ["API_ADMIN_USER"]) or r.get("device_id") == "worker-nuevo" for r in b))
        s, b = call("GET", "/api/access/events", self.admb); self.assertEqual(s, 200); self.assertEqual(b, [])
        s, b = call("GET", "/api/access/events", self.adm1); self.assertEqual(s, 200); self.assertTrue(len(b) >= 1)

    def test_08_empresa_deshabilitada(self):
        s, b = call("PUT", f"/api/admin/companies/{self.cb}", self.adm1, {"is_active": False})
        self.assertEqual(s, 403)
        s, b = call("PUT", f"/api/admin/companies/{self.cb}", self.sa, {"is_active": False}); self.assertEqual(s, 200)
        self.assertIsNone(login("admb"))
        self.assertEqual(call("GET", "/api/devices", self.admb)[0], 401)
        call("PUT", f"/api/admin/companies/{self.cb}", self.sa, {"is_active": True})
        self.assertIsNotNone(login("admb"))

    def test_09_integracion_sigue(self):
        req = urllib.request.Request(URL + "/api/integrations/access/devices")
        req.add_header("X-API-Key", os.environ["IOT_TEST_KEY"])
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)

    def test_10_modulo_viejo_solo_superadmin(self):
        self.assertEqual(call("GET", "/api/access-control/health", self.sa)[0], 200)
        self.assertEqual(call("GET", "/api/access-control/health", self.adm1)[0], 403)
        self.assertEqual(call("GET", "/api/access-control/health", self.u1)[0], 403)
        self.assertEqual(call("GET", "/api/access-control/health", self.admb)[0], 403)
        # Los eventos de acceso de la plataforma siguen disponibles, por empresa.
        self.assertEqual(call("GET", "/api/access/events", self.adm1)[0], 200)

if __name__ == "__main__":
    unittest.main(verbosity=2)
