# api/app/access_control/schemas.py

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, validator


# ============================================================
# HELPERS
# ============================================================
def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    return value or None


# ============================================================
# ACCESS USER
# ============================================================
class AccessUserBase(BaseModel):
    external_code: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    document_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = True
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    @validator("*", pre=True)
    def clean_strings(cls, v):
        if isinstance(v, str):
            return _clean(v)
        return v


class AccessUserCreate(AccessUserBase):
    pass


class AccessUserUpdate(BaseModel):
    external_code: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    document_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


class AccessUser(AccessUserBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# ============================================================
# CREDENTIAL
# ============================================================
class AccessCredentialBase(BaseModel):
    user_id: int
    credential_type: str = "card"
    credential_value: str
    is_active: bool = True

    @validator("credential_type", "credential_value")
    def required_text(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("campo requerido")
        return v


class AccessCredentialCreate(AccessCredentialBase):
    pass


class AccessCredential(AccessCredentialBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# ============================================================
# GROUP
# ============================================================
class AccessGroupBase(BaseModel):
    name: str
    description: Optional[str] = None
    is_active: bool = True

    @validator("name")
    def required_name(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("name requerido")
        return v


class AccessGroupCreate(AccessGroupBase):
    pass


class AccessGroup(AccessGroupBase):
    id: int

    class Config:
        orm_mode = True


# ============================================================
# AREA
# ============================================================
class AccessAreaBase(BaseModel):
    name: str
    code: Optional[str] = None
    parent_id: Optional[int] = None

    @validator("name")
    def required_name(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("name requerido")
        return v


class AccessAreaCreate(AccessAreaBase):
    pass


class AccessArea(AccessAreaBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# ============================================================
# DOOR
# ============================================================
class AccessDoorBase(BaseModel):
    name: str
    area_id: Optional[int] = None
    is_active: bool = True

    @validator("name")
    def required_name(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("name requerido")
        return v


class AccessDoorCreate(AccessDoorBase):
    pass


class AccessDoor(AccessDoorBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# ============================================================
# READER
# ============================================================
class AccessReaderBase(BaseModel):
    device_id: str
    reader_code: str
    door_id: int
    is_active: bool = True

    @validator("device_id", "reader_code")
    def required_text(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("campo requerido")
        return v


class AccessReaderCreate(AccessReaderBase):
    pass


class AccessReader(AccessReaderBase):
    id: int

    class Config:
        orm_mode = True


# ============================================================
# SCHEDULE
# ============================================================
class AccessScheduleBase(BaseModel):
    name: str
    is_active: bool = True

    @validator("name")
    def required_name(cls, v):
        v = _clean(v)
        if not v:
            raise ValueError("name requerido")
        return v


class AccessScheduleCreate(AccessScheduleBase):
    pass


class AccessSchedule(AccessScheduleBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


class AccessScheduleDayBase(BaseModel):
    schedule_id: int
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str
    end_time: str


class AccessScheduleDayCreate(AccessScheduleDayBase):
    pass


class AccessScheduleDay(AccessScheduleDayBase):
    id: int

    class Config:
        orm_mode = True


# ============================================================
# PERMISSION
# ============================================================
class AccessPermissionBase(BaseModel):
    user_id: Optional[int] = None
    group_id: Optional[int] = None
    door_id: Optional[int] = None
    schedule_id: Optional[int] = None
    is_active: bool = True
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    @validator("group_id")
    def user_or_group_required(cls, v, values):
        if not values.get("user_id") and not v:
            raise ValueError("Debe indicar user_id o group_id")
        return v


class AccessPermissionCreate(AccessPermissionBase):
    pass


class AccessPermission(AccessPermissionBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


# ============================================================
# HOLIDAY
# ============================================================
class AccessHolidayBase(BaseModel):
    name: str
    date: datetime
    is_active: bool = True


class AccessHolidayCreate(AccessHolidayBase):
    pass


class AccessHoliday(AccessHolidayBase):
    id: int

    class Config:
        orm_mode = True


# ============================================================
# DEVICE SYNC
# ============================================================
class AccessDeviceSyncBase(BaseModel):
    device_id: str
    config_version: Optional[int] = None
    status: Optional[str] = None
    payload: Optional[Any] = None


class AccessDeviceSyncCreate(AccessDeviceSyncBase):
    pass


class AccessDeviceSync(AccessDeviceSyncBase):
    id: int
    last_sync_at: Optional[datetime]

    class Config:
        orm_mode = True