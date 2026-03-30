from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class DeviceBase(BaseModel):
    device_id: str
    name: Optional[str] = None


class Device(DeviceBase):
    status: Optional[str] = None
    created_at: Optional[datetime]
    last_seen: Optional[datetime]

    class Config:
        orm_mode = True


class Telemetry(BaseModel):
    id: int
    device_id: str
    timestamp: datetime
    payload: Any

    class Config:
        orm_mode = True


class CommandCreate(BaseModel):
    command: str


class Command(BaseModel):
    id: int
    device_id: str
    command: str
    status: str
    created_at: datetime

    class Config:
        orm_mode = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None


class UserBase(BaseModel):
    username: str
    role: str = "user"


class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True


class UserWithPermissions(User):
    permissions: List[Permission] = []


class PermissionBase(BaseModel):
    device_id: str
    can_read: bool = False
    can_write: bool = False


class Permission(PermissionBase):
    id: int

    class Config:
        orm_mode = True
