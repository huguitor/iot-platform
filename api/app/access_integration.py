# api/app/access_integration.py
"""API de integración para la app externa de Control de Acceso.

La app de Control de Acceso es la dueña de las personas y tarjetas; esta plataforma
solo es la capa de dispositivos. Esta API le permite:

- consultar el estado de los dispositivos;
- leer los eventos que publican los equipos (/event) con un cursor por id, de modo que
  si la app estuvo caída retoma donde quedó sin perder nada;
- encolar comandos de tarjetas (alta, baja, listado...) que el mqtt-worker publica
  en iot/devices/<id>/cmd.

Autenticación: encabezado X-API-Key con la clave ACCESS_INTEGRATION_API_KEY del .env.
Es una clave distinta de la del chat (INTEGRATION_API_KEY) y, si no está definida,
la API queda deshabilitada (503).
"""
import hmac
import json
import os
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .audit import log_action
from .database import get_db
from .models import Command, Device

router = APIRouter()

# Comandos que la app de acceso puede pedir; el resto (salidas, licencias, reset) no.
ALLOWED_COMMANDS = {"enroll", "delete", "list", "sum", "version", "stats"}
CARD_COMMANDS = {"enroll", "delete"}
# Mismo límite que el firmware 2.4.0: hasta 8 dígitos, 0 < uid < 16777215
MAX_CARD_UID = 16777215
EVENT_TYPES = ("access_event_received", "mqtt_device_event_received")


def require_access_integration_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> str:
    expected = (os.getenv("ACCESS_INTEGRATION_API_KEY") or "").strip()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Access integration disabled")
    if not x_api_key or not hmac.compare_digest(expected.encode(), x_api_key.strip().encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid API key", headers={"WWW-Authenticate": "ApiKey"},
        )
    return "control-acceso"


def valid_card_uid(value) -> Optional[str]:
    value = str(value or "").strip()
    # Se aceptan ceros a la izquierda; el valor debe entrar en 24 bits (hasta 8 cifras)
    if not value.isdigit() or len(value) > 20 or not 0 < int(value) < MAX_CARD_UID:
        return None
    return str(int(value))


def device_ids_param(device_ids: Optional[str]) -> List[str]:
    if not device_ids:
        return []
    ids = [d.strip() for d in device_ids.split(",") if d.strip()]
    if len(ids) > 200 or any(len(d) > 120 for d in ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Too many or too long device_ids")
    return ids


class IntegrationCommand(BaseModel):
    cmd: str
    card_uid: Optional[str] = None
    start: Optional[int] = None
    count: Optional[int] = None

    @validator("cmd")
    def check_cmd(cls, v):
        v = (v or "").strip().lower()
        if v not in ALLOWED_COMMANDS:
            raise ValueError(f"cmd must be one of {sorted(ALLOWED_COMMANDS)}")
        return v

    @validator("count")
    def check_count(cls, v):
        if v is not None and not 1 <= v <= 50:
            raise ValueError("count must be 1..50")
        return v

    @validator("start")
    def check_start(cls, v):
        if v is not None and v < 0:
            raise ValueError("start must be >= 0")
        return v

    def payload(self) -> dict:
        data = {"cmd": self.cmd}
        if self.cmd in CARD_COMMANDS:
            uid = valid_card_uid(self.card_uid)
            if uid is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "card_uid must be digits, between 1 and 16777214",
                )
            data["card_uid"] = uid
        elif self.cmd == "list" and self.count is not None:
            data["start"] = self.start or 0
            data["count"] = self.count
        return data


def device_dict(device: Device) -> dict:
    return {
        "device_id": device.device_id,
        "status": device.status,
        "last_seen": device.last_seen,
        "fw_version": getattr(device, "fw_version", None),
        "gateway_fw_version": getattr(device, "gateway_fw_version", None),
        "card_capacity": getattr(device, "card_capacity", None),
        "last_door_sensor_state": getattr(device, "last_door_sensor_state", None),
    }


@router.get("/devices")
def list_devices(
    device_ids: Optional[str] = Query(None, description="Lista separada por comas"),
    db: Session = Depends(get_db),
    _: str = Depends(require_access_integration_key),
):
    query = select(Device).order_by(Device.device_id)
    ids = device_ids_param(device_ids)
    if ids:
        query = query.where(Device.device_id.in_(ids))
    return [device_dict(d) for d in db.execute(query).scalars().all()]


@router.get("/events/cursor")
def events_cursor(db: Session = Depends(get_db), _: str = Depends(require_access_integration_key)):
    """Último id de evento: punto de partida para no importar el historial viejo."""
    last = db.execute(text(
        "SELECT COALESCE(MAX(id), 0) FROM mqtt_events WHERE direction = 'inbound'"
    )).scalar()
    return {"last_id": int(last or 0)}


@router.get("/events")
def list_events(
    after_id: int = Query(0, ge=0),
    device_ids: Optional[str] = Query(None, description="Lista separada por comas"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: str = Depends(require_access_integration_key),
):
    """Eventos publicados por los equipos en /event, en orden de id.

    occurred_at descuenta delay_s (eventos que el gateway tuvo en cola). Los duplicados
    que el worker detecta no se devuelven: se registran con otro event_type.
    `next_after_id` es el cursor para la próxima consulta.
    """
    ids = device_ids_param(device_ids)
    params = {"after_id": after_id, "limit": limit, "types": list(EVENT_TYPES)}
    device_filter = ""
    if ids:
        device_filter = "AND device_id = ANY(:ids)"
        params["ids"] = ids
    rows = db.execute(text(f"""
        SELECT id, device_id, payload, created_at
        FROM mqtt_events
        WHERE id > :after_id
          AND direction = 'inbound'
          AND event_type = ANY(:types)
          AND topic LIKE 'iot/devices/%/event'
          {device_filter}
        ORDER BY id
        LIMIT :limit
    """), params).mappings().all()

    events = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        delay = payload.get("delay_s", 0)
        if type(delay) not in (int, float) or not 0 <= delay <= 30 * 86400:
            delay = 0
        events.append({
            "id": row["id"],
            "device_id": row["device_id"],
            "event": payload.get("event") or payload.get("event_type") or "unknown",
            "received_at": row["created_at"],
            "occurred_at": row["created_at"] - timedelta(seconds=delay),
            "payload": payload,
        })
    # Si no hubo filas válidas igual se avanza hasta la última leída
    next_after_id = rows[-1]["id"] if rows else after_id
    return {"events": events, "next_after_id": next_after_id, "more": len(rows) == limit}


@router.post("/devices/{device_id}/commands", status_code=status.HTTP_201_CREATED)
def create_command(
    device_id: str,
    body: IntegrationCommand,
    db: Session = Depends(get_db),
    source: str = Depends(require_access_integration_key),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    payload = body.payload()
    command = Command(device_id=device.device_id, command=json.dumps(payload), status="pending")
    db.add(command)
    db.commit()
    db.refresh(command)
    log_action(
        db,
        action_type="integration_command_requested",
        device_id=device.device_id,
        payload={"command_id": command.id, "command": payload, "integration": source},
        result="accepted",
    )
    return {"id": command.id, "device_id": device.device_id, "status": command.status, "command": payload}


@router.get("/commands/{command_id}")
def get_command(
    command_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_access_integration_key),
):
    command = db.get(Command, command_id)
    if not command:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
    return {
        "id": command.id, "device_id": command.device_id, "status": command.status,
        "created_at": command.created_at,
    }
