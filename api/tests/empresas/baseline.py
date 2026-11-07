"""Esquema de producción (código de master) + datos sintéticos. Solo base descartable."""
import os
assert os.environ.get("EMPRESAS_TEST_DISPOSABLE") == "1"
os.environ["API_STARTUP_READ_ONLY"] = "false"
from app import main, auth
from app.database import SessionLocal
from app.models import User, Device, UserDevicePermission, AuditLog, AccessEvent
from sqlalchemy import text

main.on_startup()  # create_all + migraciones livianas + admin inicial (como master)
db = SessionLocal()
sa = db.query(User).filter(User.username == os.environ["API_ADMIN_USER"]).one()
sa.role = "superadmin"
pw = auth.get_password_hash("Prueba-1234")
users = {}
for name, role in [("adm1", "admin"), ("adm2", "admin"), ("usr1", "user"), ("usr2", "user"), ("usr3", "user"), ("usr4", "user")]:
    u = User(username=name, hashed_password=pw, role=role, is_active=True,
             can_access_control=True, can_access_monitoring=True)
    db.add(u); db.flush(); users[name] = u
users["usr1"].created_by_user_id = users["adm1"].id
users["usr2"].created_by_user_id = users["adm2"].id
for d in ("dev-a", "dev-b", "lab-test"):
    db.add(Device(device_id=d, status="online"))
db.flush()
db.add(UserDevicePermission(user_id=users["usr1"].id, device_id="dev-a", can_read=True, can_write=True))
db.add(AuditLog(user_id=users["adm1"].id, username="adm1", action_type="login", result="success"))
db.add(AuditLog(device_id="dev-a", action_type="mqtt_state_received", result="success"))
db.add(AccessEvent(device_id="dev-a", reader="L1", event_type="access"))
db.commit()
print("BASELINE OK", db.execute(text("select count(*) from users")).scalar(), "users")
