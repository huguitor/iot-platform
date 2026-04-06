# api/app/audit.py

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from .models import AuditLog, UserSession


def utcnow():
    return datetime.now(timezone.utc)


# ============================================================
# LOG DE ACCIONES
# ============================================================
def log_action(
    db: Session,
    *,
    user=None,
    action_type: str,
    device_id: str = None,
    payload=None,
    result: str = "success",
    ip: str = None,
    user_agent: str = None,
):
    log = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action_type=action_type,
        device_id=device_id,
        payload=payload,
        result=result,
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(log)
    db.commit()


# ============================================================
# SESIONES
# ============================================================
def create_session(db: Session, user, ip=None, user_agent=None):
    session = UserSession(
        user_id=user.id,
        username=user.username,
        last_seen=utcnow(),
        ip_address=ip,
        user_agent=user_agent,
        is_active=True,
    )
    db.add(session)
    db.commit()


def touch_session(db: Session, user):
    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.is_active == True
    ).update({"last_seen": utcnow()})
    db.commit()