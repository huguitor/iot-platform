import os
from datetime import datetime, timezone
from typing import List, Optional

import redis
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from . import auth
from .database import Base, SessionLocal, engine, get_db
from .models import Command, Device, Telemetry, UserDevicePermission, User
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
)

app = FastAPI(title="IoT Platform API")

# CORS - allow dashboard origin
allowed_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.ensure_admin_user(db)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/token", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    return auth.get_token_response(form_data, db)


@app.get("/api/devices", response_model=List[DeviceSchema])
def list_devices(
    db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)
):
    if current_user.role == "admin":
        devices = db.execute(select(Device)).scalars().all()
    else:
        devices = (
            db.execute(
                select(Device)
                .join(UserDevicePermission, Device.device_id == UserDevicePermission.device_id)
                .where(
                    UserDevicePermission.user_id == current_user.id,
                    UserDevicePermission.can_read.is_(True),
                )
            )
            .scalars()
            .all()
        )
    enriched = []
    for d in devices:
        status = redis_client.get(f"device:{d.device_id}:status") or d.status
        enriched.append(DeviceSchema.from_orm(d).copy(update={"status": status}))
    return enriched


@app.get("/api/devices/{device_id}", response_model=DeviceSchema)
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
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


@app.get("/api/devices/{device_id}/telemetry", response_model=List[TelemetrySchema])
def get_telemetry(
    device_id: str,
    start: Optional[int] = Query(None, description="Unix epoch seconds from"),
    end: Optional[int] = Query(None, description="Unix epoch seconds to"),
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
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
        conditions.append(Telemetry.timestamp >= datetime.fromtimestamp(start, tz=timezone.utc))
    if end:
        conditions.append(Telemetry.timestamp <= datetime.fromtimestamp(end, tz=timezone.utc))

    query = select(Telemetry).where(and_(*conditions)).order_by(Telemetry.timestamp.desc()).limit(limit)
    rows = db.execute(query).scalars().all()
    return [TelemetrySchema.from_orm(r) for r in rows]


@app.post("/api/devices/{device_id}/command", response_model=CommandSchema)
def post_command(
    device_id: str,
    body: CommandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
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
            raise HTTPException(status_code=403, detail="Not authorized to send commands")
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        device = Device(device_id=device_id, status="pending")
        db.add(device)
        db.commit()
        db.refresh(device)

    command = Command(device_id=device.device_id, command=body.command, status="pending")
    db.add(command)
    db.commit()
    db.refresh(command)
    return CommandSchema.from_orm(command)


@app.post("/api/admin/users", response_model=UserSchema)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(auth.require_admin),
):
    existing = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=body.username,
        hashed_password=auth.get_password_hash(body.password),
        role=body.role or "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserSchema.from_orm(user)


@app.put("/api/admin/users/{user_id}/permissions", response_model=PermissionSchema)
def upsert_permission(
    user_id: int,
    body: PermissionBase,
    db: Session = Depends(get_db),
    _: User = Depends(auth.require_admin),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    device = db.execute(select(Device).where(Device.device_id == body.device_id)).scalar_one_or_none()
    if not device:
        device = Device(device_id=body.device_id, status="pending")
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
    return PermissionSchema.from_orm(permission)
