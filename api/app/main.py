# api/app/main.py

import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from . import auth
from .tenancy import company_scope, device_in_scope
from .audit import close_session, create_session, log_action, touch_session
from .database import Base, SessionLocal, engine, get_db
from .access_control.routes import router as access_control_router
from .access_integration import router as access_integration_router
from .companies import router as companies_router
from .models import (
    AccessEvent,
    AuditLog,
    Command,
    Company,
    Device,
    IntegrationAPIKey,
    Telemetry,
    User,
    UserChannel,
    UserDevicePermission,
)
from .schemas import (
    AccessEvent as AccessEventSchema,
    AuditLog as AuditLogSchema,
    Command as CommandSchema,
    CommandCreate,
    Device as DeviceSchema,
    IntegrationChatRequest,
    IntegrationChatResponse,
    ModuleAccessUpdate,
    Permission as PermissionSchema,
    PermissionBase,
    Telemetry as TelemetrySchema,
    Token,
    User as UserSchema,
    UserChannel as UserChannelSchema,
    UserChannelCreate,
    UserChannelUpdate,
    UserCreate,
    UserWithPermissions,
)

# ============================================================
# APP FASTAPI
# ============================================================
app = FastAPI(title="IoT Platform API")

# 🔥 IMPORTANTE: registrar access_control
app.include_router(
    access_control_router,
    prefix="/api/access-control",
    tags=["access-control"],
)

# Integración con la app externa de Control de Acceso (clave ACCESS_INTEGRATION_API_KEY)
app.include_router(
    access_integration_router,
    prefix="/api/integrations/access",
    tags=["integrations"],
)

# Empresas y asignación de equipos (multiempresa)
app.include_router(companies_router)

# ============================================================
# CORS
# ============================================================
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
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ============================================================
# INTEGRATION HARDENING CONFIG
# ============================================================
INTEGRATION_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("INTEGRATION_RATE_LIMIT_WINDOW_SECONDS", "60")
)
INTEGRATION_RATE_LIMIT_PER_API_KEY = int(
    os.getenv("INTEGRATION_RATE_LIMIT_PER_API_KEY", "30")
)
INTEGRATION_RATE_LIMIT_PER_CHANNEL = int(
    os.getenv("INTEGRATION_RATE_LIMIT_PER_CHANNEL", "12")
)

# ============================================================
# HELPERS
# ============================================================
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def validate_device_id(device_id: str) -> str:
    device_id = device_id.strip()
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid device_id format",
        )
    return device_id


def get_request_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def get_request_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("user-agent")


def get_device_status_safe(device_id: str, fallback: Optional[str] = None) -> Optional[str]:
    """
    Obtiene el estado del dispositivo desde Redis.

    Si Redis falla o no tiene dato:
    - usa el fallback (normalmente el valor en DB)
    - nunca rompe la API (fail-safe)
    """
    try:
        value = redis_client.get(f"device:{device_id}:status")

        if value is None:
            return fallback

        return value

    except Exception:
        # Redis caído o error → no rompemos la API
        return fallback

def mark_session_activity(db: Session, current_user: User):
    try:
        touch_session(db, current_user)
    except Exception:
        # No cortamos la request por un problema de auditoría/sesión.
        pass


# ============================================================
# HELPERS DE ROLES / JERARQUÍA
# ============================================================
def is_superadmin(user: User) -> bool:
    return user.role == "superadmin"


def is_admin_like(user: User) -> bool:
    """
    Admin-like = puede administrar usuarios.
    Incluye:
    - superadmin
    - admin
    """
    return user.role in {"superadmin", "admin"}


def require_admin_like(current_user: User):
    if not is_admin_like(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def can_create_role(current_user: User, target_role: str) -> bool:
    """
    Reglas de creación:
    - superadmin: puede crear superadmin / admin / user
    - admin: solo puede crear user
    """
    if is_superadmin(current_user):
        return target_role in {"superadmin", "admin", "user"}

    if current_user.role == "admin":
        return target_role == "user"

    return False


def can_manage_user(current_user: User, target_user: User) -> bool:
    """
    Reglas de administración:
    - superadmin: puede administrar a cualquiera
    - admin: solo usuarios que él creó
    - user: a nadie
    """
    if is_superadmin(current_user):
        return True

    if current_user.role == "admin":
        return (
            current_user.company_id is not None
            and target_user.company_id == current_user.company_id
            and target_user.role == "user"
        )

    return False


def get_visible_users_query(current_user: User, scope: Optional[int] = None):
    """
    Qué usuarios puede ver cada uno:
    - superadmin: todos, o los de la empresa elegida
    - admin: los user de su empresa
    """
    if is_superadmin(current_user):
        query = select(User).order_by(User.id)
        if scope is not None:
            query = query.where(User.company_id == scope)
        return query

    if current_user.role == "admin":
        return (
            select(User)
            .where(
                User.company_id == current_user.company_id,
                User.role == "user",
            )
            .order_by(User.id)
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin privileges required",
    )


# ============================================================
# EXTERNAL INTEGRATION AUTH
# ============================================================
API_KEY_HEADER = "X-API-Key"
INTEGRATION_API_KEY = os.getenv("INTEGRATION_API_KEY")


def normalize_channel_type(value: str) -> str:
    return value.strip().lower()


def normalize_channel_identifier(value: str, channel_type: str) -> str:
    normalized = value.strip()

    if channel_type == "whatsapp":
        normalized = re.sub(r"\D", "", normalized)
    elif channel_type == "web":
        normalized = normalized.lower()
    else:
        normalized = normalized.strip()

    return normalized


def normalize_channel_pair(channel_type: str, channel_identifier: str):
    normalized_type = normalize_channel_type(channel_type)
    normalized_identifier = normalize_channel_identifier(
        channel_identifier, normalized_type
    )
    return normalized_type, normalized_identifier


def resolve_user_by_channel(channel_type: str, channel_identifier: str, db: Session):
    normalized_type, normalized_identifier = normalize_channel_pair(
        channel_type, channel_identifier
    )

    if not normalized_type or not normalized_identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel_type and channel_identifier are required",
        )

    channels = db.execute(
        select(UserChannel).where(
            UserChannel.channel_type == normalized_type,
            UserChannel.channel_identifier == normalized_identifier,
        )
    ).scalars().all()

    if not channels:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    if len(channels) > 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Multiple users found for the same channel identifier",
        )

    channel = channels[0]

    if not channel.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Channel is disabled",
        )

    user = db.execute(select(User).where(User.id == channel.user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found for this channel",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User assigned to this channel is inactive",
        )

    return user, channel


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_valid_integration_api_key(api_key: str, db: Session):
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if INTEGRATION_API_KEY:
        expected = INTEGRATION_API_KEY.strip()
        if hmac.compare_digest(expected, api_key):
            return {"source": "env", "name": "env"}

    hashed = hash_api_key(api_key)
    key_obj = db.execute(
        select(IntegrationAPIKey).where(
            IntegrationAPIKey.key_hash == hashed,
            IntegrationAPIKey.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if key_obj:
        return {"source": "db", "name": key_obj.name, "id": key_obj.id}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def get_api_key(api_key: Optional[str] = Header(None, alias=API_KEY_HEADER)) -> str:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key


def _safe_rate_limit_key(prefix: str, raw_value: str) -> str:
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
    return f"ratelimit:{prefix}:{digest}"


def enforce_rate_limit(prefix: str, raw_value: str, limit: int, window_seconds: int):
    if limit <= 0:
        return

    key = _safe_rate_limit_key(prefix, raw_value)

    try:
        current = redis_client.incr(key)
        if current == 1:
            redis_client.expire(key, window_seconds)

        if current > limit:
            ttl = redis_client.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry in {max(ttl, 1)} seconds.",
            )
    except HTTPException:
        raise
    except Exception:
        # Fail-open para no tumbar la plataforma si Redis falla.
        return


def get_user_device_permission(db: Session, user_id: int, device_id: str):
    return db.execute(
        select(UserDevicePermission).where(
            UserDevicePermission.user_id == user_id,
            UserDevicePermission.device_id == device_id,
        )
    ).scalar_one_or_none()


def user_can_read_device(db: Session, user_id: int, device_id: str) -> bool:
    permission = get_user_device_permission(db, user_id, device_id)
    return bool(permission and permission.can_read)


def user_can_write_device(db: Session, user_id: int, device_id: str) -> bool:
    permission = get_user_device_permission(db, user_id, device_id)
    return bool(permission and permission.can_write)


def _build_device_summary(device):
    status = get_device_status_safe(device.device_id, device.status)
    return f"{device.device_id} (status={status}, last_seen={device.last_seen})"


def _request_integration_command(
    *,
    db: Session,
    user: User,
    channel: UserChannel,
    device_id: str,
    command_text: str,
) -> str:
    if not user_can_write_device(db, user.id, device_id):
        log_action(
            db,
            user=user,
            action_type="integration_command_denied",
            device_id=device_id,
            payload={
                "command": command_text,
                "reason": "not_authorized",
            },
            result="denied",
            channel_type=channel.channel_type,
            channel_identifier=channel.channel_identifier,
        )
        return "No autorizado para enviar comandos a ese dispositivo."

    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        return "Dispositivo no encontrado."

    command = Command(device_id=device_id, command=command_text, status="pending")
    db.add(command)
    db.commit()
    db.refresh(command)

    log_action(
        db,
        user=user,
        action_type="integration_command_requested",
        device_id=device_id,
        payload={
            "command_id": command.id,
            "command": command_text,
        },
        result="accepted",
        channel_type=channel.channel_type,
        channel_identifier=channel.channel_identifier,
    )

    return f"Comando '{command_text}' enviado a {device_id}."


def interpret_integration_message(user, channel, message: str, db: Session) -> str:
    text = message.strip().lower()

    if text in {"listar dispositivos", "mis dispositivos", "list devices", "devices"}:
        rows = db.execute(
            select(Device)
            .join(UserDevicePermission, Device.device_id == UserDevicePermission.device_id)
            .where(
                UserDevicePermission.user_id == user.id,
                UserDevicePermission.can_read.is_(True),
            )
            .order_by(Device.device_id)
        ).scalars().all()

        if not rows:
            return "No tiene dispositivos autorizados."

        return "Dispositivos autorizados: " + ", ".join(
            [f"{d.device_id}({get_device_status_safe(d.device_id, d.status)})" for d in rows]
        )

    status_match = re.match(r"^(?:estado|status|state) del dispositivo\s+(.+)$", text)
    if status_match:
        device_id = validate_device_id(status_match.group(1))
        if not user_can_read_device(db, user.id, device_id):
            return "No autorizado para ver ese dispositivo."

        device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
        if not device:
            return "Dispositivo no encontrado."

        telemetry = db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

        status_value = get_device_status_safe(device.device_id, device.status)
        last_payload = telemetry.payload if telemetry else None

        return (
            f"Dispositivo {device_id}: status={status_value}, last_seen={device.last_seen}, "
            f"ultima_telemetria={last_payload}"
        )

    battery_match = re.match(r"^(?:bater[ií]a|battery) del dispositivo\s+(.+)$", text)
    if battery_match:
        device_id = validate_device_id(battery_match.group(1))
        if not user_can_read_device(db, user.id, device_id):
            return "No autorizado para ver ese dispositivo."

        telemetry = db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not telemetry:
            return "No hay telemetría disponible para ese dispositivo."

        payload = telemetry.payload
        battery = payload.get("battery") if isinstance(payload, dict) else None
        battery = battery or payload.get("battery_level") if isinstance(payload, dict) else battery
        battery = battery or payload.get("battery_voltage") if isinstance(payload, dict) else battery

        if battery is None:
            return "No se encontró información de batería en la última telemetría."

        return f"Batería de {device_id}: {battery}"

    telemetry_match = re.match(
        r"^(?:ultima telemetria|última telemetría|last telemetry|telemetry) del dispositivo\s+(.+)$",
        text,
    )
    if telemetry_match:
        device_id = validate_device_id(telemetry_match.group(1))
        if not user_can_read_device(db, user.id, device_id):
            return "No autorizado para ver ese dispositivo."

        telemetry = db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not telemetry:
            return "No hay telemetría disponible para ese dispositivo."

        return f"Última telemetría de {device_id}: {telemetry.payload}"

    command_patterns = [
        (r"^activar salida\s+(.+)$", "output_on"),
        (r"^apagar salida\s+(.+)$", "output_off"),
        (r"^activar sirena\s+(.+)$", "siren_on"),
        (r"^desactivar sirena\s+(.+)$", "siren_off"),
    ]

    for pattern, command_text in command_patterns:
        match = re.match(pattern, text)
        if match:
            device_id = validate_device_id(match.group(1))
            return _request_integration_command(
                db=db,
                user=user,
                channel=channel,
                device_id=device_id,
                command_text=command_text,
            )

    return (
        "Intención no reconocida. Acciones soportadas: listar dispositivos, estado del dispositivo <id>, "
        "batería del dispositivo <id>, última telemetría del dispositivo <id>, activar salida <id>, "
        "apagar salida <id>, activar sirena <id>, desactivar sirena <id>."
    )


# ============================================================
# STARTUP
# ============================================================
@app.on_event("startup")
def on_startup():
    """
    Al iniciar la API:
    - crea tablas si no existen
    - asegura usuario admin inicial
    - agrega columnas nuevas para control de acceso (si no existen)
    """
    if os.getenv("API_STARTUP_READ_ONLY", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return

    Base.metadata.create_all(bind=engine)

    # ============================================================
    # 🔴 MIGRACIONES LIVIANAS (sin Alembic)
    # ============================================================
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_opened BOOLEAN;
        """))

        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_event_at TIMESTAMPTZ;
        """))

        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_door_reader VARCHAR(50);
        """))

        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_access_event_type VARCHAR(50);
        """))

        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_card_uid VARCHAR(100);
        """))

        conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_access_authorized BOOLEAN;
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_devices_last_door_event_at
            ON devices (last_door_event_at DESC);
        """))

    # ============================================================
    # ADMIN INICIAL
    # ============================================================
    with SessionLocal() as db:
        auth.ensure_admin_user(db)


# ============================================================
# HEALTHCHECK
# ============================================================
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ============================================================
# LOGIN / LOGOUT
# ============================================================
@app.post("/api/auth/token", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    ip = get_request_ip(request)
    user_agent = get_request_user_agent(request)

    try:
        response = auth.get_token_response(form_data, db)

        user = db.execute(
            select(User).where(User.username == form_data.username)
        ).scalar_one()

        create_session(
            db,
            user,
            ip=ip,
            user_agent=user_agent,
        )

        log_action(
            db,
            user=user,
            action_type="login_success",
            result="success",
            ip=ip,
            user_agent=user_agent,
        )

        return response

    except HTTPException:
        log_action(
            db,
            action_type="login_failed",
            result="failed",
            ip=ip,
            user_agent=user_agent,
            payload={"username": form_data.username},
        )
        raise

    except Exception as exc:
        log_action(
            db,
            action_type="login_error",
            result="failed",
            ip=ip,
            user_agent=user_agent,
            payload={
                "username": form_data.username,
                "error": str(exc),
            },
        )
        raise


@app.post("/api/auth/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    ip = get_request_ip(request)
    user_agent = get_request_user_agent(request)

    closed = close_session(db, current_user)

    log_action(
        db,
        user=current_user,
        action_type="logout",
        result="success" if closed else "no_active_session",
        ip=ip,
        user_agent=user_agent,
    )

    return {"detail": "Logged out"}


@app.get("/api/auth/me", response_model=UserSchema)
def get_authenticated_user(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    module_access = auth.get_effective_module_access(current_user)
    return UserSchema.from_orm(current_user).copy(
        update={
            "can_access_control": module_access["access_control"],
            "can_access_monitoring": module_access["monitoring"],
        }
    )


# ============================================================
# LISTAR DISPOSITIVOS
# ============================================================
@app.get("/api/devices", response_model=List[DeviceSchema])
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_any_module),
    scope: Optional[int] = Depends(company_scope),
):
    """
    - superadmin: todos, o los de la empresa elegida
    - admin: los de su empresa
    - user: los de su empresa con permiso de lectura
    """
    mark_session_activity(db, current_user)

    query = select(Device).order_by(Device.device_id)
    if scope is not None:
        query = query.where(Device.company_id == scope)

    if not is_admin_like(current_user):
        query = (
            query
            .join(
                UserDevicePermission,
                Device.device_id == UserDevicePermission.device_id,
            )
            .where(
                UserDevicePermission.user_id == current_user.id,
                UserDevicePermission.can_read.is_(True),
            )
        )

    devices = db.execute(query).scalars().all()

    enriched = []
    for d in devices:
        status_value = get_device_status_safe(d.device_id, d.status)
        enriched.append(DeviceSchema.from_orm(d).copy(update={"status": status_value}))

    return enriched


# ============================================================
# OBTENER UN DISPOSITIVO
# ============================================================
@app.get("/api/devices/{device_id}", response_model=DeviceSchema)
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_any_module),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    device_id = validate_device_id(device_id)

    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device_in_scope(device, scope):
        raise HTTPException(status_code=404, detail="Device not found")

    if not is_admin_like(current_user):
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
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view this device",
            )

    status_value = get_device_status_safe(device.device_id, device.status)
    return DeviceSchema.from_orm(device).copy(update={"status": status_value})


# ============================================================
# LEER TELEMETRÍA
# ============================================================
@app.get("/api/devices/{device_id}/telemetry", response_model=List[TelemetrySchema])
def get_telemetry(
    device_id: str,
    start: Optional[int] = Query(None, description="Unix epoch seconds from"),
    end: Optional[int] = Query(None, description="Unix epoch seconds to"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_module("monitoring")),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    device_id = validate_device_id(device_id)

    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device_in_scope(device, scope):
        raise HTTPException(status_code=404, detail="Device not found")

    if not is_admin_like(current_user):
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
            raise HTTPException(
                status_code=403,
                detail="Not authorized to read telemetry",
            )

    conditions = [Telemetry.device_id == device_id]

    if start is not None:
        conditions.append(
            Telemetry.timestamp >= datetime.fromtimestamp(start, tz=timezone.utc)
        )

    if end is not None:
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
    current_user: User = Depends(auth.require_module("monitoring")),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    device_id = validate_device_id(device_id)

    existing_device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()
    # Fuera de su empresa el equipo "no existe". Solo el superadmin puede
    # mandar comandos a un equipo que todavía no se registró.
    if existing_device is not None and not device_in_scope(existing_device, scope):
        raise HTTPException(status_code=404, detail="Device not found")
    if existing_device is None and not is_superadmin(current_user):
        raise HTTPException(status_code=404, detail="Device not found")

    if not is_admin_like(current_user):
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
            log_action(
                db,
                user=current_user,
                action_type="command_denied",
                device_id=device_id,
                payload={"command": body.command},
                result="denied",
            )
            raise HTTPException(
                status_code=403,
                detail="Not authorized to send commands",
            )

    device = db.execute(
        select(Device).where(Device.device_id == device_id)
    ).scalar_one_or_none()

    if not device:
        device = Device(device_id=device_id, status="pending", company_id=scope)
        db.add(device)
        db.commit()
        db.refresh(device)

    command = Command(
        device_id=device.device_id,
        command=body.command,
        status="pending",
    )
    db.add(command)
    db.commit()
    db.refresh(command)

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
# CREAR USUARIO (ADMIN / SUPERADMIN)
# ============================================================
@app.post("/api/admin/users", response_model=UserSchema)
def create_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    requested_role = body.role or "user"

    # Empresa del usuario nuevo: el superadmin la indica (o usa la elegida);
    # un admin solo crea usuarios en su propia empresa.
    if is_superadmin(current_user):
        target_company_id = body.company_id if body.company_id is not None else scope
        if requested_role == "superadmin":
            target_company_id = None
        elif target_company_id is None:
            raise HTTPException(status_code=400, detail="Indicá la empresa del usuario")
        elif not db.get(Company, target_company_id):
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
    else:
        target_company_id = current_user.company_id

    if not can_create_role(current_user, requested_role):
        log_action(
            db,
            user=current_user,
            action_type="user_create_denied",
            payload={
                "username": body.username,
                "requested_role": requested_role,
                "reason": "role_not_allowed",
            },
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to create this role",
        )

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
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=body.username,
        hashed_password=auth.get_password_hash(body.password),
        role=requested_role,
        company_id=target_company_id,
        created_by_user_id=current_user.id,
        is_active=body.is_active if body.is_active is not None else True,
        can_access_control=body.can_access_control,
        can_access_monitoring=body.can_access_monitoring,
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
            "created_by_user_id": current_user.id,
        },
        result="success",
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )

    return UserSchema.from_orm(user)


# ============================================================
# CREAR / ACTUALIZAR PERMISOS DE USUARIO SOBRE DISPOSITIVO
# ============================================================
@app.put("/api/admin/users/{user_id}/permissions", response_model=PermissionSchema)
def upsert_permission(
    user_id: int,
    body: PermissionBase,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)
    body.device_id = validate_device_id(body.device_id)

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
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user(current_user, user):
        log_action(
            db,
            user=current_user,
            action_type="permission_update_denied",
            device_id=body.device_id,
            payload={
                "target_user_id": user_id,
                "target_username": user.username,
                "reason": "not_manageable",
            },
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    device = db.execute(
        select(Device).where(Device.device_id == body.device_id)
    ).scalar_one_or_none()

    if device is None and not is_superadmin(current_user):
        raise HTTPException(status_code=404, detail="Device not found")

    if device is not None and device.company_id != user.company_id:
        raise HTTPException(
            status_code=400,
            detail="El dispositivo no pertenece a la empresa del usuario",
        )

    if not device:
        device = Device(
            device_id=body.device_id,
            status="pending",
            company_id=user.company_id,
        )
        db.add(device)
        db.commit()
        db.refresh(device)

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
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )

    return PermissionSchema.from_orm(permission)


@app.put("/api/admin/users/{user_id}/modules", response_model=UserSchema)
def update_module_access(
    user_id: int,
    body: ModuleAccessUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not can_manage_user(current_user, user):
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    user.can_access_control = body.can_access_control
    user.can_access_monitoring = body.can_access_monitoring
    db.commit()
    db.refresh(user)

    log_action(
        db,
        user=current_user,
        action_type="module_access_updated",
        payload={
            "target_user_id": user.id,
            "target_username": user.username,
            "can_access_control": user.can_access_control,
            "can_access_monitoring": user.can_access_monitoring,
        },
        result="success",
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )
    return UserSchema.from_orm(user)


# ============================================================
# LISTAR USUARIOS (ADMIN / SUPERADMIN)
# ============================================================
@app.get("/api/admin/users", response_model=List[UserWithPermissions])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    users = db.execute(get_visible_users_query(current_user, scope)).scalars().all()
    return users


# ============================================================
# USER CHANNELS
# ============================================================
@app.post("/api/admin/users/{user_id}/channels", response_model=UserChannelSchema)
def create_user_channel(
    user_id: int,
    body: UserChannelCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        log_action(
            db,
            user=current_user,
            action_type="channel_create_failed",
            payload={"target_user_id": user_id, "reason": "user_not_found"},
            result="failed",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user(current_user, user):
        log_action(
            db,
            user=current_user,
            action_type="channel_create_denied",
            payload={"target_user_id": user_id, "reason": "not_manageable"},
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    normalized_type, normalized_identifier = normalize_channel_pair(
        body.channel_type,
        body.channel_identifier,
    )

    existing = db.execute(
        select(UserChannel).where(
            UserChannel.channel_type == normalized_type,
            UserChannel.channel_identifier == normalized_identifier,
        )
    ).scalar_one_or_none()

    if existing:
        log_action(
            db,
            user=current_user,
            action_type="channel_create_denied",
            payload={
                "target_user_id": user_id,
                "channel_type": normalized_type,
                "channel_identifier": normalized_identifier,
                "reason": "already_exists",
            },
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=400, detail="Channel already exists")

    channel = UserChannel(
        user_id=user.id,
        channel_type=normalized_type,
        channel_identifier=normalized_identifier,
        is_enabled=body.is_enabled,
        is_verified=body.is_verified,
    )

    db.add(channel)
    db.commit()
    db.refresh(channel)

    log_action(
        db,
        user=current_user,
        action_type="channel_created",
        payload={
            "target_user_id": user.id,
            "channel_id": channel.id,
            "channel_type": channel.channel_type,
            "channel_identifier": channel.channel_identifier,
            "is_enabled": channel.is_enabled,
            "is_verified": channel.is_verified,
        },
        result="success",
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )

    return channel


@app.get("/api/admin/users/{user_id}/channels", response_model=List[UserChannelSchema])
def list_user_channels(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user(current_user, user):
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    channels = db.execute(
        select(UserChannel)
        .where(UserChannel.user_id == user_id)
        .order_by(UserChannel.id)
    ).scalars().all()

    return channels


@app.put("/api/admin/users/{user_id}/channels/{channel_id}", response_model=UserChannelSchema)
def update_user_channel(
    user_id: int,
    channel_id: int,
    body: UserChannelUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        log_action(
            db,
            user=current_user,
            action_type="channel_update_failed",
            payload={"target_user_id": user_id, "reason": "user_not_found"},
            result="failed",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user(current_user, user):
        log_action(
            db,
            user=current_user,
            action_type="channel_update_denied",
            payload={"target_user_id": user_id, "reason": "not_manageable"},
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    channel = db.execute(
        select(UserChannel).where(
            UserChannel.id == channel_id,
            UserChannel.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not channel:
        log_action(
            db,
            user=current_user,
            action_type="channel_update_failed",
            payload={
                "target_user_id": user_id,
                "channel_id": channel_id,
                "reason": "channel_not_found",
            },
            result="failed",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="Channel not found")

    if body.channel_identifier is not None:
        normalized_identifier = normalize_channel_identifier(
            body.channel_identifier,
            channel.channel_type,
        )

        duplicate = db.execute(
            select(UserChannel).where(
                UserChannel.channel_type == channel.channel_type,
                UserChannel.channel_identifier == normalized_identifier,
                UserChannel.id != channel.id,
            )
        ).scalar_one_or_none()

        if duplicate:
            log_action(
                db,
                user=current_user,
                action_type="channel_update_denied",
                payload={
                    "target_user_id": user_id,
                    "channel_id": channel_id,
                    "channel_type": channel.channel_type,
                    "channel_identifier": normalized_identifier,
                    "reason": "already_exists",
                },
                result="denied",
                ip=get_request_ip(request),
                user_agent=get_request_user_agent(request),
            )
            raise HTTPException(status_code=400, detail="Channel already exists")

        channel.channel_identifier = normalized_identifier

    if body.is_enabled is not None:
        channel.is_enabled = body.is_enabled

    if body.is_verified is not None:
        channel.is_verified = body.is_verified

    db.commit()
    db.refresh(channel)

    log_action(
        db,
        user=current_user,
        action_type="channel_updated",
        payload={
            "target_user_id": user_id,
            "channel_id": channel.id,
            "channel_type": channel.channel_type,
            "channel_identifier": channel.channel_identifier,
            "is_enabled": channel.is_enabled,
            "is_verified": channel.is_verified,
        },
        result="success",
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )

    return channel


@app.delete("/api/admin/users/{user_id}/channels/{channel_id}")
def delete_user_channel(
    user_id: int,
    channel_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        log_action(
            db,
            user=current_user,
            action_type="channel_delete_failed",
            payload={"target_user_id": user_id, "reason": "user_not_found"},
            result="failed",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user(current_user, user):
        log_action(
            db,
            user=current_user,
            action_type="channel_delete_denied",
            payload={"target_user_id": user_id, "reason": "not_manageable"},
            result="denied",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to manage this user",
        )

    channel = db.execute(
        select(UserChannel).where(
            UserChannel.id == channel_id,
            UserChannel.user_id == user_id,
        )
    ).scalar_one_or_none()

    if not channel:
        log_action(
            db,
            user=current_user,
            action_type="channel_delete_failed",
            payload={
                "target_user_id": user_id,
                "channel_id": channel_id,
                "reason": "channel_not_found",
            },
            result="failed",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
        raise HTTPException(status_code=404, detail="Channel not found")

    deleted_payload = {
        "target_user_id": user_id,
        "channel_id": channel.id,
        "channel_type": channel.channel_type,
        "channel_identifier": channel.channel_identifier,
    }

    db.delete(channel)
    db.commit()

    log_action(
        db,
        user=current_user,
        action_type="channel_deleted",
        payload=deleted_payload,
        result="success",
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )

    return {"detail": "Channel deleted"}


# ============================================================
# AUDIT
# ============================================================
@app.get("/api/admin/audit", response_model=List[AuditLogSchema])
def get_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    user: Optional[str] = None,
    device: Optional[str] = None,
    action: Optional[str] = None,
    channel_type: Optional[str] = None,
    channel_identifier: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    query = select(AuditLog)
    if scope is not None:
        query = query.where(AuditLog.company_id == scope)

    if user:
        query = query.where(AuditLog.username == user)

    if device:
        query = query.where(AuditLog.device_id == device)

    if action:
        query = query.where(AuditLog.action_type == action)

    if channel_type:
        query = query.where(AuditLog.channel_type == channel_type)

    if channel_identifier:
        query = query.where(AuditLog.channel_identifier == channel_identifier)

    query = query.order_by(AuditLog.created_at.desc()).limit(limit)

    logs = db.execute(query).scalars().all()
    return logs

# ============================================================
# CONTROL DE ACCESO - EVENTOS
# ============================================================
@app.get("/api/access/events", response_model=List[AccessEventSchema])
def get_access_events(
   device_id: Optional[str] = Query(None),
   reader: Optional[str] = Query(None),
   card_uid: Optional[str] = Query(None),
   event_type: Optional[str] = Query(None),
   authorized: Optional[bool] = Query(None),
   date_from: Optional[int] = Query(None, description="Unix epoch seconds desde"),
   date_to: Optional[int] = Query(None, description="Unix epoch seconds hasta"),
   limit: int = Query(200, ge=1, le=1000),
   db: Session = Depends(get_db),
   current_user: User = Depends(auth.require_module("access_control")),
   scope: Optional[int] = Depends(company_scope),
):
   """
   Lista eventos de control de acceso.

   Permisos:
   - superadmin/admin: ven todos los eventos.
   - user: solo ve eventos de dispositivos con can_read=True.
   """
   mark_session_activity(db, current_user)

   query = select(AccessEvent)
   if scope is not None:
       query = query.where(AccessEvent.company_id == scope)

   # ----------------------------
   # Filtro por permisos
   # ----------------------------
   if is_admin_like(current_user):
       pass
   else:
       query = (
           query
           .join(
               UserDevicePermission,
               AccessEvent.device_id == UserDevicePermission.device_id,
           )
           .where(
               UserDevicePermission.user_id == current_user.id,
               UserDevicePermission.can_read.is_(True),
           )
       )

   # ----------------------------
   # Filtros opcionales
   # ----------------------------
   if device_id:
       device_id = validate_device_id(device_id)

       if not is_admin_like(current_user):
           if not user_can_read_device(db, current_user.id, device_id):
               raise HTTPException(
                   status_code=status.HTTP_403_FORBIDDEN,
                   detail="Not authorized to view access events for this device",
               )

       query = query.where(AccessEvent.device_id == device_id)

   if reader:
       query = query.where(AccessEvent.reader == reader)

   if card_uid:
       query = query.where(AccessEvent.card_uid == card_uid)

   if event_type:
       query = query.where(AccessEvent.event_type == event_type)

   if authorized is not None:
       query = query.where(AccessEvent.authorized.is_(authorized))

   if date_from is not None:
       query = query.where(
           AccessEvent.created_at >= datetime.fromtimestamp(date_from, tz=timezone.utc)
       )

   if date_to is not None:
       query = query.where(
           AccessEvent.created_at <= datetime.fromtimestamp(date_to, tz=timezone.utc)
       )

   query = query.order_by(AccessEvent.created_at.desc()).limit(limit)

   rows = db.execute(query).scalars().all()

   return rows

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
    current_user: User = Depends(auth.get_current_user),
    scope: Optional[int] = Depends(company_scope),
):
    mark_session_activity(db, current_user)
    current_user = require_admin_like(current_user)

    conditions = []
    params = {}

    if scope is not None:
        conditions.append(
            "device_id IN (SELECT device_id FROM devices WHERE company_id = :company_id)"
        )
        params["company_id"] = scope

    if device_id:
        device_id = validate_device_id(device_id)
        conditions.append("device_id = :device_id")
        params["device_id"] = device_id

    if date_from is not None:
        conditions.append("created_at >= :date_from")
        params["date_from"] = datetime.fromtimestamp(date_from, tz=timezone.utc)

    if date_to is not None:
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

    rows = db.execute(text(sql), params).mappings().all()

    return [dict(row) for row in rows]


@app.post("/api/integrations/chat", response_model=IntegrationChatResponse)
def integration_chat(
    request: IntegrationChatRequest,
    api_key: str = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    normalized_type, normalized_identifier = normalize_channel_pair(
        request.channel_type,
        request.channel_identifier,
    )

    try:
        integration_key = get_valid_integration_api_key(api_key, db)

        enforce_rate_limit(
            prefix="integration_api_key",
            raw_value=hash_api_key(api_key),
            limit=INTEGRATION_RATE_LIMIT_PER_API_KEY,
            window_seconds=INTEGRATION_RATE_LIMIT_WINDOW_SECONDS,
        )
        enforce_rate_limit(
            prefix="integration_channel",
            raw_value=f"{normalized_type}:{normalized_identifier}",
            limit=INTEGRATION_RATE_LIMIT_PER_CHANNEL,
            window_seconds=INTEGRATION_RATE_LIMIT_WINDOW_SECONDS,
        )

        user, channel = resolve_user_by_channel(
            normalized_type,
            normalized_identifier,
            db,
        )

        response_text = interpret_integration_message(user, channel, request.message, db)

        log_action(
            db,
            user=user,
            action_type="integration_chat_requested",
            payload={
                "channel_type": normalized_type,
                "channel_identifier": normalized_identifier,
                "message": request.message,
                "response": response_text,
                "integration_source": integration_key["source"],
                "integration_name": integration_key["name"],
            },
            result="success",
            channel_type=normalized_type,
            channel_identifier=normalized_identifier,
        )

        return IntegrationChatResponse(response=response_text)

    except HTTPException as exc:
        action_type = (
            "integration_chat_denied"
            if exc.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            else "integration_chat_failed"
        )

        log_action(
            db,
            action_type=action_type,
            payload={
                "channel_type": normalized_type,
                "channel_identifier": normalized_identifier,
                "message": request.message,
                "error": exc.detail,
            },
            result="failed",
            channel_type=normalized_type,
            channel_identifier=normalized_identifier,
        )
        raise

    except Exception as exc:
        log_action(
            db,
            action_type="integration_chat_failed",
            payload={
                "channel_type": normalized_type,
                "channel_identifier": normalized_identifier,
                "message": request.message,
                "error": str(exc),
            },
            result="failed",
            channel_type=normalized_type,
            channel_identifier=normalized_identifier,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Integration processing error",
        )
