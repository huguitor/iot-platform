# api/app/tenancy.py
"""Separación de datos por empresa (multiempresa).

Reglas:
- superadmin: no tiene empresa. Elige con qué empresa trabajar enviando el
  encabezado X-Company-Id. Sin encabezado ve todas (solo en vistas de plataforma).
- admin y user: siempre trabajan con la empresa de su usuario; el encabezado se ignora.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from . import auth
from .database import get_db
from .models import Company, Device, User

COMPANY_HEADER = "X-Company-Id"


def is_superadmin(user: User) -> bool:
    return user.role == "superadmin"


def resolve_company_scope(request: Request, user: User, db: Session) -> Optional[int]:
    """Empresa con la que trabaja el pedido; None = todas (solo superadmin)."""
    if is_superadmin(user):
        raw = (request.headers.get(COMPANY_HEADER) or "").strip()
        if not raw:
            return None
        try:
            company_id = int(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Company-Id inválido")
        if not db.get(Company, company_id):
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
        return company_id

    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El usuario no tiene empresa asignada",
        )
    return user.company_id


def company_scope(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Optional[int]:
    return resolve_company_scope(request, current_user, db)


def require_company_scope(scope: Optional[int] = Depends(company_scope)) -> int:
    """Para datos que siempre pertenecen a una empresa (control de acceso)."""
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Elegí una empresa para trabajar",
        )
    return scope


def device_in_scope(device: Optional[Device], scope: Optional[int]) -> bool:
    if device is None:
        return False
    return scope is None or device.company_id == scope
