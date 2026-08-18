# api/app/access_control/service.py

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models, schemas


def _commit_or_400(db: Session, obj):
    try:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registro duplicado o referencia inválida",
        ) from exc


# ============================================================
# ACCESS USERS
# ============================================================
def create_access_user(db: Session, body: schemas.AccessUserCreate):
    obj = models.AccessUser(**body.dict())
    return _commit_or_400(db, obj)


def list_access_users(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessUser)
        .order_by(models.AccessUser.id.desc())
        .limit(limit)
    ).scalars().all()


# ============================================================
# CREDENTIALS
# ============================================================
def create_credential(db: Session, body: schemas.AccessCredentialCreate):
    user = db.get(models.AccessUser, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Access user not found")

    obj = models.AccessCredential(**body.dict())
    return _commit_or_400(db, obj)


def list_credentials(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessCredential)
        .order_by(models.AccessCredential.id.desc())
        .limit(limit)
    ).scalars().all()


# ============================================================
# GROUPS
# ============================================================
def create_group(db: Session, body: schemas.AccessGroupCreate):
    obj = models.AccessGroup(**body.dict())
    return _commit_or_400(db, obj)


def list_groups(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessGroup)
        .order_by(models.AccessGroup.name)
        .limit(limit)
    ).scalars().all()


# ============================================================
# AREAS
# ============================================================
def create_area(db: Session, body: schemas.AccessAreaCreate):
    obj = models.AccessArea(**body.dict())
    return _commit_or_400(db, obj)


def list_areas(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessArea)
        .order_by(models.AccessArea.name)
        .limit(limit)
    ).scalars().all()


# ============================================================
# DOORS
# ============================================================
def create_door(db: Session, body: schemas.AccessDoorCreate):
    obj = models.AccessDoor(**body.dict())
    return _commit_or_400(db, obj)


def list_doors(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessDoor)
        .order_by(models.AccessDoor.id.desc())
        .limit(limit)
    ).scalars().all()


# ============================================================
# READERS
# ============================================================
def create_reader(db: Session, body: schemas.AccessReaderCreate):
    door = db.get(models.AccessDoor, body.door_id)
    if not door:
        raise HTTPException(status_code=404, detail="Door not found")

    obj = models.AccessReader(**body.dict())
    return _commit_or_400(db, obj)


def list_readers(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessReader)
        .order_by(models.AccessReader.id.desc())
        .limit(limit)
    ).scalars().all()

# ============================================================
# USER GROUPS
# ============================================================
def assign_user_to_group(db: Session, user_id: int, group_id: int):
    user = db.get(models.AccessUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Access user not found")

    group = db.get(models.AccessGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Access group not found")

    obj = models.AccessUserGroup(user_id=user_id, group_id=group_id)
    return _commit_or_400(db, obj)


def list_user_groups(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessUserGroup)
        .order_by(models.AccessUserGroup.id.desc())
        .limit(limit)
    ).scalars().all()


# ============================================================
# SCHEDULES
# ============================================================
def create_schedule(db: Session, body: schemas.AccessScheduleCreate):
    obj = models.AccessSchedule(**body.dict())
    return _commit_or_400(db, obj)


def list_schedules(db: Session, limit: int = 100):
    return db.execute(
        select(models.AccessSchedule)
        .order_by(models.AccessSchedule.name)
        .limit(limit)
    ).scalars().all()


def create_schedule_day(db: Session, body: schemas.AccessScheduleDayCreate):
    schedule = db.get(models.AccessSchedule, body.schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    obj = models.AccessScheduleDay(**body.dict())
    return _commit_or_400(db, obj)


def list_schedule_days(db: Session, limit: int = 200):
    return db.execute(
        select(models.AccessScheduleDay)
        .order_by(models.AccessScheduleDay.schedule_id, models.AccessScheduleDay.day_of_week)
        .limit(limit)
    ).scalars().all()


# ============================================================
# PERMISSIONS
# ============================================================
def create_permission(db: Session, body: schemas.AccessPermissionCreate):
    if body.user_id is None and body.group_id is None:
        raise HTTPException(
            status_code=400,
            detail="Debe indicar user_id o group_id",
        )

    if body.user_id is not None:
        user = db.get(models.AccessUser, body.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Access user not found")

    if body.group_id is not None:
        group = db.get(models.AccessGroup, body.group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Access group not found")

    if body.door_id is not None:
        door = db.get(models.AccessDoor, body.door_id)
        if not door:
            raise HTTPException(status_code=404, detail="Door not found")

    if body.schedule_id is not None:
        schedule = db.get(models.AccessSchedule, body.schedule_id)
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

    obj = models.AccessPermission(**body.dict())
    return _commit_or_400(db, obj)


def list_permissions(db: Session, limit: int = 200):
    return db.execute(
        select(models.AccessPermission)
        .order_by(models.AccessPermission.id.desc())
        .limit(limit)
    ).scalars().all()

# ============================================================
# DEVICE SYNC (CRÍTICO)
# ============================================================
def build_device_sync(db: Session, device_id: str):
    # 1. Readers del dispositivo
    readers = db.execute(
        select(models.AccessReader)
        .where(
            models.AccessReader.device_id == device_id,
            models.AccessReader.is_active == True,
        )
    ).scalars().all()

    if not readers:
        raise HTTPException(status_code=404, detail="No readers for device")

    door_ids = list({r.door_id for r in readers})

    # 2. Permisos activos de esas puertas
    permissions = db.execute(
        select(models.AccessPermission)
        .where(
            models.AccessPermission.door_id.in_(door_ids),
            models.AccessPermission.is_active == True,
        )
    ).scalars().all()

    # 3. Users directos
    user_ids = {p.user_id for p in permissions if p.user_id}

    # 4. Groups → Users
    group_ids = {p.group_id for p in permissions if p.group_id}

    if group_ids:
        group_users = db.execute(
            select(models.AccessUserGroup)
            .where(models.AccessUserGroup.group_id.in_(group_ids))
        ).scalars().all()

        for gu in group_users:
            user_ids.add(gu.user_id)

    if not user_ids:
        return {
            "device_id": device_id,
            "readers": [],
            "cards": [],
            "schedules": [],
        }

    # 5. Users
    users = db.execute(
        select(models.AccessUser)
        .where(
            models.AccessUser.id.in_(user_ids),
            models.AccessUser.is_active == True,
        )
    ).scalars().all()

    user_map = {u.id: u for u in users}

    # 6. Credenciales
    credentials = db.execute(
        select(models.AccessCredential)
        .where(
            models.AccessCredential.user_id.in_(user_ids),
            models.AccessCredential.is_active == True,
        )
    ).scalars().all()

    # 7. Schedules
    schedule_ids = {p.schedule_id for p in permissions if p.schedule_id}

    schedules = db.execute(
        select(models.AccessSchedule)
        .where(models.AccessSchedule.id.in_(schedule_ids))
    ).scalars().all()

    schedule_days = db.execute(
        select(models.AccessScheduleDay)
        .where(models.AccessScheduleDay.schedule_id.in_(schedule_ids))
    ).scalars().all()

    schedule_map = {}
    for s in schedules:
        schedule_map[s.id] = {
            "id": s.id,
            "days": [],
        }

    for d in schedule_days:
        if d.schedule_id in schedule_map:
            schedule_map[d.schedule_id]["days"].append({
                "day_of_week": d.day_of_week,
                "start_time": d.start_time,
                "end_time": d.end_time,
            })

    # 8. Readers salida
    readers_out = [
        {
            "reader_code": r.reader_code,
            "door_id": r.door_id,
        }
        for r in readers
    ]

    # 9. Cards (LO IMPORTANTE)
    cards = []

    for cred in credentials:
        user = user_map.get(cred.user_id)
        if not user:
            continue

        user_permissions = [
            p for p in permissions
            if p.user_id == user.id or p.group_id in group_ids
        ]

        doors = []
        for p in user_permissions:
            reader_match = [
                r for r in readers if r.door_id == p.door_id
            ]

            for r in reader_match:
                doors.append({
                    "reader_code": r.reader_code,
                    "schedule_id": p.schedule_id,
                })

        if doors:
            cards.append({
                "card_uid": cred.credential_value,
                "user_id": user.id,
                "user_name": user.full_name,
                "doors": doors,
            })

    return {
        "device_id": device_id,
        "readers": readers_out,
        "cards": cards,
        "schedules": list(schedule_map.values()),
    }

