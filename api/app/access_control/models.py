# api/app/access_control/models.py

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


# ============================================================
# ACCESS USERS (personas físicas)
# ============================================================
class AccessUser(Base):
    __tablename__ = "access_users"

    id = Column(Integer, primary_key=True, index=True)

    external_code = Column(String(50), unique=True, index=True)

    first_name = Column(String(100))
    last_name = Column(String(100))
    full_name = Column(String(200))

    document_number = Column(String(50))
    email = Column(String(150))
    phone = Column(String(50))

    is_active = Column(Boolean, default=True, index=True)

    valid_from = Column(DateTime(timezone=True))
    valid_until = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    credentials = relationship("AccessCredential", back_populates="user", cascade="all, delete-orphan")
    groups = relationship("AccessUserGroup", back_populates="user", cascade="all, delete-orphan")


# ============================================================
# ACCESS CREDENTIALS (RFID por ahora)
# ============================================================
class AccessCredential(Base):
    __tablename__ = "access_credentials"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("access_users.id"), nullable=False)

    credential_type = Column(String(50), default="card")
    credential_value = Column(String(150), nullable=False, unique=True)

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("AccessUser", back_populates="credentials")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_single_credential"),
    )


# ============================================================
# ACCESS GROUPS
# ============================================================
class AccessGroup(Base):
    __tablename__ = "access_groups"

    id = Column(Integer, primary_key=True)

    name = Column(String(150), unique=True, nullable=False)
    description = Column(String)

    is_active = Column(Boolean, default=True)

    users = relationship("AccessUserGroup", back_populates="group", cascade="all, delete-orphan")


# ============================================================
# USER ↔ GROUP RELATION
# ============================================================
class AccessUserGroup(Base):
    __tablename__ = "access_user_groups"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("access_users.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("access_groups.id"), nullable=False)

    user = relationship("AccessUser", back_populates="groups")
    group = relationship("AccessGroup", back_populates="users")

    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_user_group"),
    )


# ============================================================
# AREAS
# ============================================================
class AccessArea(Base):
    __tablename__ = "access_areas"

    id = Column(Integer, primary_key=True)

    name = Column(String(100), nullable=False)
    code = Column(String(50))

    parent_id = Column(Integer, ForeignKey("access_areas.id"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# DOORS (puertas físicas)
# ============================================================
class AccessDoor(Base):
    __tablename__ = "access_doors"

    id = Column(Integer, primary_key=True)

    name = Column(String(150), nullable=False)

    area_id = Column(Integer, ForeignKey("access_areas.id"))

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    readers = relationship("AccessReader", back_populates="door", cascade="all, delete-orphan")


# ============================================================
# READERS (L1 / L2)
# ============================================================
class AccessReader(Base):
    __tablename__ = "access_readers"

    id = Column(Integer, primary_key=True)

    device_id = Column(String(120), nullable=False)  # usa devices.device_id existente
    reader_code = Column(String(50), nullable=False)  # L1 / L2

    door_id = Column(Integer, ForeignKey("access_doors.id"), nullable=False)

    is_active = Column(Boolean, default=True)

    door = relationship("AccessDoor", back_populates="readers")

    __table_args__ = (
        UniqueConstraint("device_id", "reader_code", name="uq_device_reader"),
    )


# ============================================================
# SCHEDULES
# ============================================================
class AccessSchedule(Base):
    __tablename__ = "access_schedules"

    id = Column(Integer, primary_key=True)

    name = Column(String(150), unique=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    days = relationship("AccessScheduleDay", back_populates="schedule", cascade="all, delete-orphan")


# ============================================================
# SCHEDULE DAYS
# ============================================================
class AccessScheduleDay(Base):
    __tablename__ = "access_schedule_days"

    id = Column(Integer, primary_key=True)

    schedule_id = Column(Integer, ForeignKey("access_schedules.id"), nullable=False)

    day_of_week = Column(Integer)  # 0-6
    start_time = Column(String(8))
    end_time = Column(String(8))

    schedule = relationship("AccessSchedule", back_populates="days")


# ============================================================
# PERMISSIONS
# ============================================================
class AccessPermission(Base):
    __tablename__ = "access_permissions"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("access_users.id"))
    group_id = Column(Integer, ForeignKey("access_groups.id"))

    door_id = Column(Integer, ForeignKey("access_doors.id"))
    schedule_id = Column(Integer, ForeignKey("access_schedules.id"))

    is_active = Column(Boolean, default=True)

    valid_from = Column(DateTime(timezone=True))
    valid_until = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# HOLIDAYS
# ============================================================
class AccessHoliday(Base):
    __tablename__ = "access_holidays"

    id = Column(Integer, primary_key=True)

    name = Column(String(150))
    date = Column(DateTime)

    is_active = Column(Boolean, default=True)


# ============================================================
# DEVICE SYNC
# ============================================================
class AccessDeviceSync(Base):
    __tablename__ = "access_device_sync"

    id = Column(Integer, primary_key=True)

    device_id = Column(String(120), nullable=False)

    last_sync_at = Column(DateTime(timezone=True))
    config_version = Column(Integer)

    status = Column(String(50))
    payload = Column(JSONB)