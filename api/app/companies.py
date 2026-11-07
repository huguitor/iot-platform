# api/app/companies.py
"""Empresas (multiempresa) y asignación de dispositivos a empresas."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth
from .audit import log_action
from .database import get_db
from .models import Company, Device, User, UserDevicePermission
from .schemas import (
    Company as CompanySchema,
    CompanyCreate,
    CompanyUpdate,
    Device as DeviceSchema,
    DeviceAdminUpdate,
)
from .tenancy import company_scope, device_in_scope, is_superadmin

router = APIRouter(prefix="/api/admin", tags=["companies"])


def _require_superadmin(user: User) -> User:
    if not is_superadmin(user):
        raise HTTPException(status_code=403, detail="Solo el superadmin administra empresas")
    return user


def _request_meta(request: Request) -> dict:
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


# ============================================================
# EMPRESAS
# ============================================================
@router.get("/companies", response_model=List[CompanySchema])
def list_companies(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """superadmin: todas. Resto: solo la propia."""
    query = select(Company).order_by(Company.name)
    if not is_superadmin(current_user):
        query = query.where(Company.id == current_user.company_id)
    return db.execute(query).scalars().all()


@router.post("/companies", response_model=CompanySchema)
def create_company(
    body: CompanyCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    _require_superadmin(current_user)
    company = Company(**body.dict())
    db.add(company)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una empresa con ese nombre o código")
    db.refresh(company)
    log_action(
        db, user=current_user, action_type="company_created", result="success",
        payload={"company_id": company.id, "name": company.name, "code": company.code},
        **_request_meta(request),
    )
    return company


@router.put("/companies/{company_id}", response_model=CompanySchema)
def update_company(
    company_id: int,
    body: CompanyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    _require_superadmin(current_user)
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    changes = body.dict(exclude_unset=True)
    if "name" in changes and not changes["name"]:
        raise HTTPException(status_code=400, detail="El nombre no puede quedar vacío")
    for key, value in changes.items():
        setattr(company, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una empresa con ese nombre")
    db.refresh(company)
    log_action(
        db, user=current_user, action_type="company_updated", result="success",
        payload={"company_id": company.id, "changes": {k: v for k, v in changes.items()}},
        **_request_meta(request),
    )
    return company


# ============================================================
# DISPOSITIVOS: empresa y nombre
# ============================================================
@router.put("/devices/{device_id}", response_model=DeviceSchema)
def update_device(
    device_id: str,
    body: DeviceAdminUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
    scope: Optional[int] = Depends(company_scope),
):
    """
    - superadmin: asigna o cambia la empresa (set_company=true) y el nombre.
    - admin: solo cambia el nombre de equipos de su empresa.
    Al cambiar de empresa se borran los permisos de usuarios de la empresa anterior.
    """
    if current_user.role not in {"superadmin", "admin"}:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device_in_scope(device, scope):
        raise HTTPException(status_code=404, detail="Device not found")

    previous_company = device.company_id
    if body.set_company:
        _require_superadmin(current_user)
        if body.company_id is not None and not db.get(Company, body.company_id):
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
        device.company_id = body.company_id

    if "name" in body.__fields_set__:
        device.name = body.name

    removed_permissions = 0
    if body.set_company and previous_company != device.company_id:
        removed_permissions = (
            db.query(UserDevicePermission)
            .filter(UserDevicePermission.device_id == device.device_id)
            .delete(synchronize_session=False)
        )

    db.commit()
    db.refresh(device)
    log_action(
        db, user=current_user, action_type="device_updated", device_id=device.device_id,
        result="success",
        payload={
            "name": device.name,
            "previous_company_id": previous_company,
            "company_id": device.company_id,
            "removed_permissions": removed_permissions,
        },
        **_request_meta(request),
    )
    return DeviceSchema.from_orm(device)
