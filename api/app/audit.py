# api/app/audit.py

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import AuditLog, UserSession


# ============================================================
# TIME
# ============================================================
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
    device_id: Optional[str] = None,
    payload=None,
    result: str = "success",
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    channel_type: Optional[str] = None,
    channel_identifier: Optional[str] = None,
):
    """
    Registra un evento de auditoría general.
    """

    log = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action_type=action_type,
        device_id=device_id,
        payload=payload,
        result=result,
        ip_address=ip,
        user_agent=user_agent,
        channel_type=channel_type,
        channel_identifier=channel_identifier,
    )

    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ============================================================
# SESIONES
# ============================================================
def create_session(db: Session, user, ip=None, user_agent=None):
    """
    Crea una nueva sesión activa para el usuario.
    """

    session = UserSession(
        user_id=user.id,
        username=user.username,
        login_at=utcnow(),
        last_seen=utcnow(),
        ip_address=ip,
        user_agent=user_agent,
        is_active=True,
    )

    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_active_session(db: Session, user):
    """
    Devuelve la sesión activa más reciente del usuario.
    """

    return (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.is_active.is_(True),
        )
        .order_by(UserSession.login_at.desc())
        .first()
    )


def touch_session(db: Session, user):
    """
    Actualiza last_seen de la sesión activa más reciente.
    """

    session = get_active_session(db, user)
    if not session:
        return None

    session.last_seen = utcnow()
    db.commit()
    db.refresh(session)
    return session


def close_session(db: Session, user):
    """
    Cierra la sesión activa más reciente del usuario.
    """

    session = get_active_session(db, user)
    if not session:
        return None

    now = utcnow()
    session.last_seen = now
    session.logout_at = now
    session.is_active = False

    db.commit()
    db.refresh(session)
    return session


def close_all_user_sessions(db: Session, user):
    """
    Cierra todas las sesiones activas del usuario.
    Útil para desactivar usuario, resetear acceso o forzar logout general.
    """

    sessions = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.is_active.is_(True),
        )
        .all()
    )

    if not sessions:
        return 0

    now = utcnow()

    for session in sessions:
        session.last_seen = now
        session.logout_at = now
        session.is_active = False

    db.commit()
    return len(sessions)