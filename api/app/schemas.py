# api/app/schemas.py

import re
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, validator


VALID_ROLES = {"superadmin", "admin", "user"}
VALID_CHANNEL_TYPES = {"web", "whatsapp", "telegram", "openclaw"}

DEVICE_ID_REGEX = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9._-]{3,120}$")

MAX_CHANNEL_IDENTIFIER_LENGTH = 255
MAX_INTEGRATION_MESSAGE_LENGTH = 500


# ============================================================
# HELPERS
# ============================================================
def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    return value.strip()


def _require_non_empty(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    return value


# ============================================================
# DEVICE
# ============================================================
class DeviceBase(BaseModel):
    device_id: str
    name: Optional[str] = None

    @validator("device_id")
    def validate_device_id(cls, v):
        v = _require_non_empty(v, "device_id")
        if not DEVICE_ID_REGEX.fullmatch(v):
            raise ValueError("Invalid device_id format")
        return v

    @validator("name")
    def normalize_name(cls, v):
        return _normalize_text(v)


class Device(DeviceBase):
    status: Optional[str] = None
    created_at: Optional[datetime]
    last_seen: Optional[datetime]

    # ============================================================
    # 🔴 ESTADO ACTUAL CONTROL DE ACCESO (NUEVO)
    # ============================================================
    last_door_opened: Optional[bool] = None
    last_door_event_at: Optional[datetime] = None
    last_door_reader: Optional[str] = None

    last_access_event_type: Optional[str] = None
    last_card_uid: Optional[str] = None
    last_access_authorized: Optional[bool] = None
        # ============================================================
    # 🔴 SENSOR MAGNÉTICO REAL
    # ============================================================
    last_door_sensor_state: Optional[str] = None
    last_door_sensor_at: Optional[datetime] = None

    class Config:
        orm_mode = True

# ============================================================
# TELEMETRY
# ============================================================
class Telemetry(BaseModel):
    id: int
    device_id: str
    timestamp: datetime
    payload: Any

    class Config:
        orm_mode = True


# ============================================================
# COMMANDS
# ============================================================
class CommandCreate(BaseModel):
    command: str

    @validator("command")
    def validate_command(cls, v):
        v = _require_non_empty(v, "command")
        if len(v) > 255:
            raise ValueError("command is too long")
        return v


class Command(BaseModel):
    id: int
    device_id: str
    command: str
    status: str
    created_at: datetime

    class Config:
        orm_mode = True


# ============================================================
# AUTH
# ============================================================
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None


# ============================================================
# USER CHANNELS
# ============================================================
class UserChannelBase(BaseModel):
    channel_type: str
    channel_identifier: str
    is_enabled: bool = True
    is_verified: bool = False

    @validator("channel_type")
    def validate_channel_type(cls, v):
        v = _require_non_empty(v, "channel_type").lower()
        if v not in VALID_CHANNEL_TYPES:
            raise ValueError(
                f"channel_type must be one of: {', '.join(sorted(VALID_CHANNEL_TYPES))}"
            )
        return v

    @validator("channel_identifier")
    def validate_channel_identifier(cls, v):
        v = _require_non_empty(v, "channel_identifier")
        if len(v) > MAX_CHANNEL_IDENTIFIER_LENGTH:
            raise ValueError("channel_identifier is too long")
        return v


class UserChannelCreate(UserChannelBase):
    pass


class UserChannelUpdate(BaseModel):
    channel_identifier: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_verified: Optional[bool] = None

    @validator("channel_identifier")
    def validate_channel_identifier(cls, v):
        if v is None:
            return v
        v = _require_non_empty(v, "channel_identifier")
        if len(v) > MAX_CHANNEL_IDENTIFIER_LENGTH:
            raise ValueError("channel_identifier is too long")
        return v


class UserChannel(UserChannelBase):
    id: int

    class Config:
        orm_mode = True


# ============================================================
# USERS
# ============================================================
class UserBase(BaseModel):
    username: str
    role: str = "user"
    is_active: Optional[bool] = True

    @validator("username")
    def validate_username(cls, v):
        v = _require_non_empty(v, "username")
        if not USERNAME_REGEX.fullmatch(v):
            raise ValueError(
                "username must be 3-120 chars and contain only letters, numbers, dot, underscore or hyphen"
            )
        return v

    @validator("role")
    def validate_role(cls, v):
        v = _require_non_empty(v, "role").lower()
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


class UserCreate(UserBase):
    password: str

    @validator("password")
    def validate_password(cls, v):
        v = _require_non_empty(v, "password")
        if len(v) < 6:
            raise ValueError("password must be at least 6 characters")
        if len(v) > 255:
            raise ValueError("password is too long")
        return v


class User(UserBase):
    id: int
    created_at: Optional[datetime]
    created_by_user_id: Optional[int] = None
    channels: List[UserChannel] = Field(default_factory=list)

    class Config:
        orm_mode = True


# ============================================================
# PERMISSIONS
# ============================================================
class PermissionBase(BaseModel):
    device_id: str
    can_read: bool = False
    can_write: bool = False

    @validator("device_id")
    def validate_device_id(cls, v):
        v = _require_non_empty(v, "device_id")
        if not DEVICE_ID_REGEX.fullmatch(v):
            raise ValueError("Invalid device_id format")
        return v


class Permission(PermissionBase):
    id: int

    class Config:
        orm_mode = True


class UserWithPermissions(User):
    permissions: List[Permission] = Field(default_factory=list)


# ============================================================
# AUDIT
# ============================================================
class AuditLog(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    action_type: str
    device_id: Optional[str]
    payload: Optional[Any]
    result: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    channel_type: Optional[str]
    channel_identifier: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


class IntegrationChatRequest(BaseModel):
    channel_type: str
    channel_identifier: str
    message: str

    @validator("channel_type")
    def validate_channel_type(cls, v):
        v = _require_non_empty(v, "channel_type").lower()
        if v not in VALID_CHANNEL_TYPES:
            raise ValueError(
                f"channel_type must be one of: {', '.join(sorted(VALID_CHANNEL_TYPES))}"
            )
        return v

    @validator("channel_identifier")
    def validate_channel_identifier(cls, v):
        v = _require_non_empty(v, "channel_identifier")
        if len(v) > MAX_CHANNEL_IDENTIFIER_LENGTH:
            raise ValueError("channel_identifier is too long")
        return v

    @validator("message")
    def validate_message(cls, v):
        v = _require_non_empty(v, "message")
        if len(v) > MAX_INTEGRATION_MESSAGE_LENGTH:
            raise ValueError("message is too long")
        return v


class IntegrationChatResponse(BaseModel):
    response: str


class UserSession(BaseModel):
    id: int
    username: str
    login_at: datetime
    last_seen: Optional[datetime]
    is_active: bool

    class Config:
        orm_mode = True

# ============================================================
# ACCESS EVENTS
# ============================================================
class AccessEvent(BaseModel):
   id: int
   device_id: Optional[str]
   reader: Optional[str]
   event_type: Optional[str]
   card_uid: Optional[str]
   authorized: Optional[bool]
   door_opened: Optional[bool]
   payload: Optional[Any]
   created_at: datetime

   class Config:
       orm_mode = True
