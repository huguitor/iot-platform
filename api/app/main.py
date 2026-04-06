# api/app/main.py

import os
from datetime import datetime, timezone
from typing import List, Optional

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from . import auth
from .audit import create_session, log_action
from .database import Base, SessionLocal, engine, get_db
from .models import Command, Device, Telemetry, UserDevicePermission, User, AuditLog
from .schemas import (
    Command as CommandSchema,
    CommandCreate,
    Device as DeviceSchema,
    Permission as PermissionSchema,
    PermissionBase,
    Telemetry as TelemetrySchema,
    Token,
    User as UserSchema,
    UserCreate,
    UserWithPermissions,
)

# ============================================================
# APP FASTAPI
# ============================================================
app = FastAPI(title="IoT Platform API")

# ============================================================
# CORS
# ============================================================
# Permitimos orígenes definidos por variable de entorno.
# Si no se define nada, queda "*", útil para pruebas,
# aunque en producción conviene restringirlo.
allowed_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# REDIS
# ============================================================
# Redis se usa para leer el estado rápido de los dispositivos,
# por ejemplo online/offline actualizado por el worker MQTT.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


# ============================================================
# STARTUP
# ============================================================
@app.on_event("startup")
def on_startup():
    """
    Al iniciar la API:
    - crea tablas si no existen
    - asegura usuario admin inicial
    """
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        auth.ensure_admin_user(db)


# ============================================================
# HEALTHCHECK
# ============================================================
@app.get("/api/health")
def health():
    """
    Endpoint simple para verificar si la API está viva.
    """
    return {"status": "ok"}


# ============================================================
# LOGIN / TOKEN
# ============================================================
@app.post("/api/auth/token", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Autentica usuario y devuelve JWT.

    Además:
    - registra sesión en user_sessions
    - registra auditoría de login_success
    - registra auditoría de login_failed si falla

    IMPORTANTE:
    auth.get_token_response() ya valida usuario/clave y genera token.
    """
    try:
        # Valida credenciales y arma la respuesta JWT
        response = auth.get_token_response(form_data, db)

        # Recuperamos el usuario para auditar y crear sesión
        user = db.execute(
            select(User).where(User.username == form_data.username)
        ).scalar_one()

        # Creamos registro de sesión web
        create_session(
            db,
            user,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        # Registramos auditoría de login exitoso
        log_action(
            db,
            user=user,
            action_type="login_success",
            result="success",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return response

    except HTTPException:
        # Login fallido por credenciales o validación
        log_action(
            db,
            action_type="login_failed",
            result="failed",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            payload={"username": form_data.username},
        )
        raise

    except Exception as exc:
        # Error inesperado también queda auditado
        log_action(
            db,
            action_type="login_error",
            result="failed",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            payload={
                "username": form_data.username,
                "error": str(exc),
            },
        )
        raise


# ============================================================
# LISTAR DISPOSITIVOS
# ============================================================
@app.get("/api/devices", response_model=List[DeviceSchema])
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """
    Lista dispositivos visibles para el usuario actual.

    - admin: ve todos
    - user común: ve solo los que tengan permiso de lectura
    """
    if current_user.role == "admin":
        devices = db.execute(
            select(Device).order_by(Device.device_id)
        ).scalars().all()
    else:
        devices = (
            db.execute(
                select(Device)
                .join(UserDevicePermission, Device.device_id == UserDevicePermission.device_id)
                .where(
                    UserDevicePermission.user_id == current_user.id,
                    UserDevicePermission.can_read.is_(True),
                )
                .order_by(Device.device_id)
            )
            .scalars()
            .all()
        )

    # Enriquecemos con estado vivo desde Redis si existe;
    # si no, usamos el de DB.
    enriched = []
    for d in devices:
        status = redis_client.get(f"device:{d.device_id}:status") or d.status
        enriched.append(DeviceSchema.from_orm(d).copy(update={"status": status}))

    return enriched


# ============================================================
# OBTENER UN DISPOSITIVO
# ============================================================
@app.get("/api/devices/{device_id}", response_model=DeviceSchema)
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """
    Devuelve un dispositivo puntual.

    - verifica existencia
    - si no es admin, valida permiso de lectura
    """
    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if current_user.role != "admin":
        perm = (
            db.execute(
                select(UserDevicePermission).where(
                    UserDevicePermission.user_id == current_user.id,
                    UserDevicePermission.device_id == device_id,
                    UserDevicePermission.can_read.is_(True),
                )
            )
            .scalar_one_or_none()
        )

        if not perm:
            raise HTTPException(status_code=403, detail="Not authorized to view this device")

    status = redis_client.get(f"device:{device.device_id}:status") or device.status
    return DeviceSchema.from_orm(device).copy(update={"status": status})


# ============================================================
# LEER TELEMETRÍA
# ============================================================
@app.get("/api/devices/{device_id}/telemetry", response_model=List[TelemetrySchema])
def get_telemetry(
    device_id: str,
    start: Optional[int] = Query(None, description="Unix epoch seconds from"),
    end: Optional[int] = Query(None, description="Unix epoch seconds to"),
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """
    Devuelve telemetría de un dispositivo.

    Parámetros:
    - start: timestamp unix desde
    - end: timestamp unix hasta
    - limit: máximo de registros

    Seguridad:
    - admin: acceso total
    - user común: necesita can_read
    """
    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if current_user.role != "admin":
        perm = (
            db.execute(
                select(UserDevicePermission).where(
                    UserDevicePermission.user_id == current_user.id,
                    UserDevicePermission.device_id == device_id,
                    UserDevicePermission.can_read.is_(True),
                )
            )
            .scalar_one_or_none()
        )

        if not perm:
            raise HTTPException(status_code=403, detail="Not authorized to read telemetry")

    conditions = [Telemetry.device_id == device_id]

    if start:
        conditions.append(
            Telemetry.timestamp >= datetime.fromtimestamp(start, tz=timezone.utc)
        )

    if end:
        conditions.append(
            Telemetry.timestamp <= datetime.fromtimestamp(end, tz=timezone.utc)
        )

    query = (
        select(Telemetry)
        .where(and_(*conditions))
        .order_by(Telemetry.timestamp.desc())
        .limit(limit)
    )

    rows = db.execute(query).scalars().all()
    return [TelemetrySchema.from_orm(r) for r in rows]


# ============================================================
# ENVIAR COMANDO A DISPOSITIVO
# ============================================================
@app.post("/api/devices/{device_id}/command", response_model=CommandSchema)
def post_command(
    device_id: str,
    body: CommandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """
    Encola un comando para un dispositivo.

    Flujo:
    1. verifica permisos
    2. crea device si aún no existe
    3. inserta comando en tabla commands con status pending
    4. registra auditoría

    El worker MQTT después levantará ese comando y lo despachará al broker.
    """
    if current_user.role != "admin":
        perm = (
            db.execute(
                select(UserDevicePermission).where(
                    UserDevicePermission.user_id == current_user.id,
                    UserDevicePermission.device_id == device_id,
                    UserDevicePermission.can_write.is_(True),
                )
            )
            .scalar_one_or_none()
        )

        if not perm:
            # Registramos intento rechazado
            log_action(
                db,
                user=current_user,
                action_type="command_denied",
                device_id=device_id,
                payload={"command": body.command},
                result="denied",
            )
            raise HTTPException(status_code=403, detail="Not authorized to send commands")

    # Si el dispositivo no existe aún, lo creamos en estado pending
    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device:
        device = Device(device_id=device_id, status="pending")
        db.add(device)
        db.commit()
        db.refresh(device)

    # Encolamos el comando
    command = Command(
        device_id=device.device_id,
        command=body.command,
        status="pending",
    )
    db.add(command)
    db.commit()
    db.refresh(command)

    # Registramos la solicitud aceptada
    log_action(
        db,
        user=current_user,
        action_type="command_requested",
        device_id=device_id,
        payload={
            "command_id": command.id,
            "command": body.command,
        },
        result="accepted",
    )

    return CommandSchema.from_orm(command)


# ============================================================
# CREAR USUARIO (ADMIN)
# ============================================================
@app.post("/api/admin/users", response_model=UserSchema)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_admin),
):
    """
    Crea un usuario nuevo.

    Solo admin.
    También deja auditoría del alta.
    """
    existing = db.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()

    if existing:
        log_action(
            db,
            user=current_user,
            action_type="user_create_denied",
            payload={"username": body.username, "reason": "already_exists"},
            result="denied",
        )
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=body.username,
        hashed_password=auth.get_password_hash(body.password),
        role=body.role or "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_action(
        db,
        user=current_user,
        action_type="user_created",
        payload={
            "created_user_id": user.id,
            "created_username": user.username,
            "role": user.role,
        },
        result="success",
    )

    return UserSchema.from_orm(user)


# ============================================================
# CREAR / ACTUALIZAR PERMISOS DE USUARIO SOBRE DISPOSITIVO
# ============================================================
@app.put("/api/admin/users/{user_id}/permissions", response_model=PermissionSchema)
def upsert_permission(
    user_id: int,
    body: PermissionBase,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_admin),
):
    """
    Inserta o actualiza permisos de un usuario sobre un dispositivo.

    Solo admin.
    También deja auditoría del cambio.
    """
    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        log_action(
            db,
            user=current_user,
            action_type="permission_update_failed",
            device_id=body.device_id,
            payload={"target_user_id": user_id, "reason": "user_not_found"},
            result="failed",
        )
        raise HTTPException(status_code=404, detail="User not found")

    # Si el dispositivo no existe, lo damos de alta como pending
    device = db.execute(
        select(Device).where(Device.device_id == body.device_id)
    ).scalar_one_or_none()

    if not device:
        device = Device(device_id=body.device_id, status="pending")
        db.add(device)
        db.commit()
        db.refresh(device)

    # Buscamos si ya existe permiso
    permission = (
        db.execute(
            select(UserDevicePermission).where(
                UserDevicePermission.user_id == user_id,
                UserDevicePermission.device_id == body.device_id,
            )
        )
        .scalar_one_or_none()
    )

    if permission:
        permission.can_read = body.can_read
        permission.can_write = body.can_write
    else:
        permission = UserDevicePermission(
            user_id=user_id,
            device_id=body.device_id,
            can_read=body.can_read,
            can_write=body.can_write,
        )
        db.add(permission)

    db.commit()
    db.refresh(permission)

    log_action(
        db,
        user=current_user,
        action_type="permission_updated",
        device_id=body.device_id,
        payload={
            "target_user_id": user_id,
            "target_username": user.username,
            "can_read": body.can_read,
            "can_write": body.can_write,
        },
        result="success",
    )

    return PermissionSchema.from_orm(permission)


# ============================================================
# LISTAR USUARIOS (ADMIN)
# ============================================================
@app.get("/api/admin/users", response_model=List[UserWithPermissions])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_admin),
):
    """
    Lista usuarios con permisos.
    Solo admin.
    """
    users = db.execute(select(User)).scalars().all()
    return users

@app.get("/api/admin/audit")
def get_audit_logs(
    limit: int = 100,
    user: Optional[str] = None,
    device: Optional[str] = None,
    action: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_admin),
):

    query = select(AuditLog)

    if user:
        query = query.where(AuditLog.username == user)

    if device:
        query = query.where(AuditLog.device_id == device)

    if action:
        query = query.where(AuditLog.action_type == action)

    query = query.order_by(AuditLog.created_at.desc()).limit(limit)

    logs = db.execute(query).scalars().all()

    return logs

# ============================================================
# HISTÓRICO MQTT
# ============================================================
@app.get("/api/admin/mqtt-events")
def get_mqtt_events(
    device_id: Optional[str] = None,
    date_from: Optional[int] = Query(None, description="Unix epoch seconds desde"),
    date_to: Optional[int] = Query(None, description="Unix epoch seconds hasta"),
    event_type: Optional[str] = Query(None, description="Tipo de evento MQTT"),
    limit: int = Query(200, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_admin),
):
    """
    Devuelve eventos MQTT históricos desde la tabla mqtt_events.

    Filtros soportados:
    - device_id
    - date_from (unix timestamp)
    - date_to (unix timestamp)
    - event_type
    - limit
    """
    conditions = []
    params = {}

    if device_id:
        conditions.append("device_id = :device_id")
        params["device_id"] = device_id

    if date_from:
        conditions.append("created_at >= :date_from")
        params["date_from"] = datetime.fromtimestamp(date_from, tz=timezone.utc)

    if date_to:
        conditions.append("created_at <= :date_to")
        params["date_to"] = datetime.fromtimestamp(date_to, tz=timezone.utc)

    if event_type:
        conditions.append("event_type = :event_type")
        params["event_type"] = event_type

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            id,
            device_id,
            direction,
            topic,
            payload,
            event_type,
            processed_ok,
            related_command_id,
            created_at
        FROM mqtt_events
        {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit
    """

    params["limit"] = limit

    from sqlalchemy import text

    rows = db.execute(text(sql), params).mappings().all()

    return [dict(row) for row in rows]