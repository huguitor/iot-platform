# api/app/provisioning.py
"""Alta automática de equipos: fábrica, reclamo por QR, liberación y baja MQTT.

Ver docs/alta-automatica-equipos.md. Reglas (decisiones de Hugo, plan §1 y §9):
- El equipo sale de fábrica con su usuario MQTT (Dynamic Security) y queda
  SIN empresa. Solo el superadmin lo ve.
- El administrador de una empresa lo reclama con el ID y el código del QR.
  El código sirve una sola vez.
- La empresa ve el historial solo desde que el equipo es suyo (`company_since`).
- Ni el secreto MQTT ni el código de reclamo se guardan en claro ni van a logs:
  se devuelven una sola vez.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, dynsec
from .audit import log_action
from .database import get_db
from .models import Device, User, UserDevicePermission
from .schemas import (
    FACTORY_DEVICE_ID_REGEX,
    FACTORY_MODELS,
    Device as DeviceSchema,
    DeviceClaim,
    DeviceClaimCode,
    DeviceMqttUpdate,
    FactoryDeviceCreate,
    FactoryDeviceCreated,
)

router = APIRouter(tags=["provisioning"])

FACTORY_TOKEN = (os.getenv("FACTORY_API_TOKEN") or "").strip()
CLAIM_BASE_URL = os.getenv("CLAIM_BASE_URL", "https://iot.panozoelectronica.com.ar").rstrip("/")
CLAIM_WINDOW = int(os.getenv("CLAIM_RATE_LIMIT_WINDOW_SECONDS", "900"))
CLAIM_PER_USER = int(os.getenv("CLAIM_RATE_LIMIT_PER_USER", "10"))
CLAIM_PER_IP = int(os.getenv("CLAIM_RATE_LIMIT_PER_IP", "30"))
CLAIM_PER_DEVICE = int(os.getenv("CLAIM_RATE_LIMIT_PER_DEVICE", "10"))

# Sin 0/O, 1/I/L: se puede copiar a mano de la etiqueta. 12 caracteres ≈ 59 bits.
CLAIM_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CLAIM_LENGTH = 12

_redis = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
# Hash fijo para comparar aunque el equipo no exista (mismo tiempo de respuesta).
_DUMMY_HASH = auth.get_password_hash("no-existe-" + secrets.token_hex(8))

INVALID_CLAIM = "Equipo o código inválido"


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta(request: Request) -> dict:
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _new_claim_code() -> str:
    return "".join(secrets.choice(CLAIM_ALPHABET) for _ in range(CLAIM_LENGTH))


def _format_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _claim_url(device_id: str, code: str) -> str:
    return f"{CLAIM_BASE_URL}/agregar?d={device_id}&c={code}"


def _rate_limit(prefix: str, raw: str, limit: int) -> None:
    """Igual que la integración: si Redis falla, deja pasar (el código tiene ~59 bits y bcrypt)."""
    if limit <= 0:
        return
    key = "ratelimit:%s:%s" % (prefix, hashlib.sha256(raw.encode()).hexdigest())
    try:
        current = _redis.incr(key)
        if current == 1:
            _redis.expire(key, CLAIM_WINDOW)
        if current > limit:
            ttl = _redis.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Demasiados intentos. Probá de nuevo en {max(ttl, 1)} segundos.",
            )
    except HTTPException:
        raise
    except Exception:
        return


def _dynsec_call(fn, *args):
    try:
        return fn(*args)
    except dynsec.DynsecNotConfigured:
        raise HTTPException(status_code=503, detail="Alta automática no configurada en el broker")
    except dynsec.DynsecError as exc:
        raise HTTPException(status_code=502, detail=f"Broker MQTT: {exc}")


def set_device_company(db: Session, device: Device, company_id: Optional[int]) -> int:
    """Cambia la empresa del equipo. Devuelve cuántos permisos de usuarios se borraron."""
    if device.company_id == company_id:
        return 0
    device.company_id = company_id
    device.company_since = _now() if company_id is not None else None
    # El QR deja de servir apenas el equipo tiene empresa (se reclame o se asigne).
    if company_id is not None:
        device.claim_code_hash = None
    return (
        db.query(UserDevicePermission)
        .filter(UserDevicePermission.device_id == device.device_id)
        .delete(synchronize_session=False)
    )


# ------------------------------------------------------------
# quién puede usar la fábrica
# ------------------------------------------------------------
def _factory_actor(
    request: Request,
    x_factory_token: Optional[str] = Header(None, alias="X-Factory-Token"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Token de fábrica (notebook) o superadmin con su sesión. None = token de fábrica."""
    if x_factory_token is not None:
        if FACTORY_TOKEN and hmac.compare_digest(x_factory_token.strip(), FACTORY_TOKEN):
            return None
        raise HTTPException(status_code=401, detail="Token de fábrica inválido")
    authz = request.headers.get("authorization") or ""
    if not authz.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta X-Factory-Token o sesión de superadmin")
    user = auth.get_current_user(token=authz[7:].strip(), db=db)
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Solo fábrica o superadmin")
    return user


# ============================================================
# FÁBRICA
# ============================================================
@router.post("/api/factory/devices", response_model=FactoryDeviceCreated)
def factory_create_device(
    body: FactoryDeviceCreate,
    request: Request,
    reprovision: bool = False,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(_factory_actor),
):
    """
    Registra un equipo nuevo SIN empresa, crea su usuario MQTT y devuelve el
    secreto y el código de reclamo una sola vez.

    `?reprovision=true`: el equipo ya está fabricado y sigue sin empresa (p. ej.
    se perdió el secreto al grabarlo). Genera secreto y código nuevos.
    """
    device_id = body.device_id
    model = FACTORY_MODELS[device_id.split("-", 1)[0]]
    secret = secrets.token_urlsafe(32)
    code = _new_claim_code()
    actor_name = actor.username if actor else "factory-token"

    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if device is not None:
        if not reprovision:
            raise HTTPException(status_code=409, detail="El equipo ya existe")
        if device.provisioned_at is None:
            raise HTTPException(status_code=409, detail="Existe un equipo con ese ID que no es de fábrica")
        if device.company_id is not None:
            raise HTTPException(status_code=409, detail="El equipo ya tiene empresa: liberarlo antes")
        if _dynsec_call(dynsec.get_client, device_id) is None:
            _dynsec_call(dynsec.create_device_client, device_id, secret)
        else:
            _dynsec_call(dynsec.set_password, device_id, secret)
            _dynsec_call(dynsec.enable_client, device_id)
        device.claim_code_hash = auth.get_password_hash(code)
        device.mqtt_enabled = True
        device.provisioned_at = _now()
        if body.name:
            device.name = body.name
        db.commit()
        action = "device_reprovisioned"
    else:
        if reprovision:
            raise HTTPException(status_code=404, detail="Device not found")
        if _dynsec_call(dynsec.get_client, device_id) is not None:
            raise HTTPException(status_code=409, detail="El usuario MQTT ya existe en el broker")
        _dynsec_call(dynsec.create_device_client, device_id, secret)
        device = Device(
            device_id=device_id,
            name=body.name,
            status="offline",
            company_id=None,
            model=model,
            provisioned_at=_now(),
            claim_code_hash=auth.get_password_hash(code),
            mqtt_enabled=True,
        )
        db.add(device)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            try:
                dynsec.delete_client(device_id)
            except dynsec.DynsecError:
                pass
            raise HTTPException(status_code=409, detail="El equipo ya existe")
        except Exception:
            db.rollback()
            try:
                dynsec.delete_client(device_id)
            except dynsec.DynsecError:
                pass
            raise
        action = "device_provisioned"

    log_action(
        db, user=actor, action_type=action, device_id=device_id, result="success",
        payload={"model": model, "by": actor_name}, **_meta(request),
    )
    return FactoryDeviceCreated(
        device_id=device_id,
        model=model,
        mqtt_username=device_id,
        mqtt_password=secret,
        claim_code=_format_code(code),
        claim_url=_claim_url(device_id, code),
    )


# ============================================================
# RECLAMO POR QR (administrador de la empresa)
# ============================================================
@router.post("/api/devices/claim", response_model=DeviceSchema)
def claim_device(
    body: DeviceClaim,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    if current_user.role != "admin":
        detail = ("El superadmin asigna equipos desde la administración"
                  if current_user.role == "superadmin" else "Solo el administrador de la empresa")
        raise HTTPException(status_code=403, detail=detail)
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="El usuario no tiene empresa asignada")

    meta = _meta(request)
    _rate_limit("claim-user", str(current_user.id), CLAIM_PER_USER)
    _rate_limit("claim-ip", meta["ip"] or "-", CLAIM_PER_IP)
    _rate_limit("claim-device", body.device_id, CLAIM_PER_DEVICE)

    device = None
    if FACTORY_DEVICE_ID_REGEX.fullmatch(body.device_id):
        device = db.execute(
            select(Device).where(Device.device_id == body.device_id).with_for_update()
        ).scalar_one_or_none()
    usable = (
        device is not None
        and device.provisioned_at is not None
        and device.company_id is None
        and device.claim_code_hash is not None
        and len(body.claim_code) == CLAIM_LENGTH
    )
    valid = auth.verify_password(body.claim_code, device.claim_code_hash if usable else _DUMMY_HASH)
    if not (usable and valid):
        db.rollback()
        log_action(
            db, user=current_user, action_type="device_claim_failed", result="denied",
            payload={"device_id": body.device_id[:40]}, **meta,
        )
        raise HTTPException(status_code=400, detail=INVALID_CLAIM)

    set_device_company(db, device, current_user.company_id)
    db.commit()
    db.refresh(device)
    log_action(
        db, user=current_user, action_type="device_claimed", device_id=device.device_id,
        result="success", payload={"company_id": device.company_id}, **meta,
    )
    return DeviceSchema.from_orm(device)


# ============================================================
# SUPERADMIN: liberar y usuario MQTT
# ============================================================
def _get_device(db: Session, device_id: str) -> Device:
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("/api/admin/devices/{device_id}/release", response_model=DeviceClaimCode)
def release_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_superadmin),
):
    """Deja el equipo sin empresa y genera un código de reclamo nuevo (etiqueta nueva)."""
    device = _get_device(db, device_id)
    if device.provisioned_at is None:
        raise HTTPException(status_code=400, detail="Solo equipos de fábrica; para los demás quitar la empresa")
    previous = device.company_id
    removed = set_device_company(db, device, None)
    code = _new_claim_code()
    device.claim_code_hash = auth.get_password_hash(code)
    db.commit()
    log_action(
        db, user=current_user, action_type="device_released", device_id=device.device_id,
        result="success", payload={"previous_company_id": previous, "removed_permissions": removed},
        **_meta(request),
    )
    return DeviceClaimCode(
        device_id=device.device_id, claim_code=_format_code(code), claim_url=_claim_url(device.device_id, code)
    )


@router.put("/api/admin/devices/{device_id}/mqtt", response_model=DeviceSchema)
def set_device_mqtt(
    device_id: str,
    body: DeviceMqttUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.require_superadmin),
):
    """Habilita o deshabilita el usuario MQTT (robo, baja). Deshabilitar corta la conexión."""
    device = _get_device(db, device_id)
    if device.provisioned_at is None:
        raise HTTPException(status_code=400, detail="El equipo no es de fábrica: su usuario MQTT se maneja a mano")
    _dynsec_call(dynsec.enable_client if body.enabled else dynsec.disable_client, device.device_id)
    device.mqtt_enabled = body.enabled
    db.commit()
    db.refresh(device)
    log_action(
        db, user=current_user, action_type="device_mqtt_enabled" if body.enabled else "device_mqtt_disabled",
        device_id=device.device_id, result="success", **_meta(request),
    )
    return DeviceSchema.from_orm(device)
