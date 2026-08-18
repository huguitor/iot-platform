# api/app/access_control/routes.py

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import auth
from ..models import User

from . import schemas, service

router = APIRouter()


# ============================================================
# HEALTH
# ============================================================
@router.get("/health")
def health():
    return {"status": "ok", "module": "access_control"}


# ============================================================
# USERS
# ============================================================
@router.post("/users", response_model=schemas.AccessUser)
def create_user(
    body: schemas.AccessUserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_access_user(db, body)


@router.get("/users", response_model=List[schemas.AccessUser])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_access_users(db)


# ============================================================
# CREDENTIALS
# ============================================================
@router.post("/credentials", response_model=schemas.AccessCredential)
def create_credential(
    body: schemas.AccessCredentialCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_credential(db, body)


@router.get("/credentials", response_model=List[schemas.AccessCredential])
def list_credentials(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_credentials(db)


# ============================================================
# GROUPS
# ============================================================
@router.post("/groups", response_model=schemas.AccessGroup)
def create_group(
    body: schemas.AccessGroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_group(db, body)


@router.get("/groups", response_model=List[schemas.AccessGroup])
def list_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_groups(db)


# ============================================================
# AREAS
# ============================================================
@router.post("/areas", response_model=schemas.AccessArea)
def create_area(
    body: schemas.AccessAreaCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_area(db, body)


@router.get("/areas", response_model=List[schemas.AccessArea])
def list_areas(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_areas(db)


# ============================================================
# DOORS
# ============================================================
@router.post("/doors", response_model=schemas.AccessDoor)
def create_door(
    body: schemas.AccessDoorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_door(db, body)


@router.get("/doors", response_model=List[schemas.AccessDoor])
def list_doors(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_doors(db)


# ============================================================
# READERS
# ============================================================
@router.post("/readers", response_model=schemas.AccessReader)
def create_reader(
    body: schemas.AccessReaderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_reader(db, body)


@router.get("/readers", response_model=List[schemas.AccessReader])
def list_readers(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_readers(db)

# ============================================================
# USER GROUPS
# ============================================================
@router.post("/user-groups")
def assign_user_to_group(
    user_id: int,
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.assign_user_to_group(db, user_id, group_id)


@router.get("/user-groups")
def list_user_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_user_groups(db)


# ============================================================
# SCHEDULES
# ============================================================
@router.post("/schedules", response_model=schemas.AccessSchedule)
def create_schedule(
    body: schemas.AccessScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_schedule(db, body)


@router.get("/schedules", response_model=List[schemas.AccessSchedule])
def list_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_schedules(db)


# ============================================================
# SCHEDULE DAYS
# ============================================================
@router.post("/schedule-days", response_model=schemas.AccessScheduleDay)
def create_schedule_day(
    body: schemas.AccessScheduleDayCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_schedule_day(db, body)


@router.get("/schedule-days", response_model=List[schemas.AccessScheduleDay])
def list_schedule_days(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_schedule_days(db)


# ============================================================
# PERMISSIONS
# ============================================================
@router.post("/permissions", response_model=schemas.AccessPermission)
def create_permission(
    body: schemas.AccessPermissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.create_permission(db, body)


@router.get("/permissions", response_model=List[schemas.AccessPermission])
def list_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    return service.list_permissions(db)
@router.get("/sync/device/{device_id}")
def sync_device(device_id: str, db: Session = Depends(get_db)):
    return service.build_device_sync(db, device_id)
