# api/app/models.py

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base


# ============================================================
# DEVICE
# ============================================================
class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(120), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=True)
    status = Column(String(50), default="offline")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), index=True)

    # ============================================================
    # 🔴 ESTADO ACTUAL DE CONTROL DE ACCESO (NUEVO)
    # ============================================================
    last_door_opened = Column(Boolean, nullable=True)  # True=abierta, False=cerrada
    last_door_event_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_door_reader = Column(String(50), nullable=True)

    last_access_event_type = Column(String(50), nullable=True)  # access / door_state
    last_card_uid = Column(String(100), nullable=True)
    last_access_authorized = Column(Boolean, nullable=True)
    # ============================================================
# 🔴 ESTADO ACTUAL DE CONTROL DE ACCESO (NUEVO)
# ============================================================
    last_door_opened = Column(Boolean, nullable=True)
    last_door_event_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_door_reader = Column(String(50), nullable=True)

    last_access_event_type = Column(String(50), nullable=True)
    last_card_uid = Column(String(100), nullable=True)
    last_access_authorized = Column(Boolean, nullable=True)

    # ============================================================
    # 🔴 SENSOR MAGNÉTICO REAL
    # ============================================================
    last_door_sensor_state = Column(String(50), nullable=True)
    last_door_sensor_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # ============================================================
    # RELACIONES
    # ============================================================
    telemetry = relationship("Telemetry", back_populates="device")
    commands = relationship("Command", back_populates="device")
    permissions = relationship("UserDevicePermission", back_populates="device")

# ============================================================
# TELEMETRY
# ============================================================
class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(120),
        ForeignKey("devices.device_id"),
        nullable=False,
        index=True,
    )
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    payload = Column(JSONB, nullable=False)

    device = relationship("Device", back_populates="telemetry")


# ============================================================
# COMMANDS
# ============================================================
class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(120),
        ForeignKey("devices.device_id"),
        nullable=False,
        index=True,
    )

    command = Column(String(255), nullable=False)
    status = Column(String(50), default="pending", index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    device = relationship("Device", back_populates="commands")


# ============================================================
# USERS
# ============================================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(120), unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

    role = Column(String(50), default="user", index=True)

    # Permisos de entrada a módulos. Los permisos por dispositivo siguen
    # representados por UserDevicePermission.
    can_access_control = Column(Boolean, default=False, nullable=False)
    can_access_monitoring = Column(Boolean, default=True, nullable=False)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True, index=True)

    permissions = relationship(
        "UserDevicePermission",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    creator = relationship("User", remote_side=[id], backref="created_users")

    channels = relationship(
        "UserChannel",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    sessions = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )


# ============================================================
# USER CHANNELS
# ============================================================
class UserChannel(Base):
    __tablename__ = "user_channels"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    channel_type = Column(String(50), nullable=False, index=True)
    channel_identifier = Column(String(255), nullable=False, index=True)

    is_enabled = Column(Boolean, default=True, index=True)
    is_verified = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="channels")

    # 🔴 IMPORTANTE: evita duplicados
    __table_args__ = (
        UniqueConstraint(
            "channel_type",
            "channel_identifier",
            name="uq_channel_type_identifier",
        ),
    )


# ============================================================
# PERMISSIONS
# ============================================================
class UserDevicePermission(Base):
    __tablename__ = "user_device_permissions"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(120), ForeignKey("devices.device_id"), nullable=False, index=True)

    can_read = Column(Boolean, default=False, nullable=False)
    can_write = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="permissions")
    device = relationship("Device", back_populates="permissions")

    # 🔴 IMPORTANTE: evita duplicados
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_user_device"),
    )


# ============================================================
# USER SESSIONS
# ============================================================
class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    username = Column(String(120))

    login_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    logout_at = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), index=True)

    ip_address = Column(String(100))
    user_agent = Column(String(255))

    is_active = Column(Boolean, default=True, index=True)

    user = relationship("User", back_populates="sessions")


# ============================================================
# AUDIT LOGS
# ============================================================
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(120), nullable=True)

    action_type = Column(String(100), index=True)
    device_id = Column(String(120), nullable=True, index=True)

    payload = Column(JSONB, nullable=True)
    result = Column(String(50), index=True)

    ip_address = Column(String(100), nullable=True)
    user_agent = Column(String(255), nullable=True)

    # 🔴 NUEVO (clave para WhatsApp / OpenClaw)
    channel_type = Column(String(50), nullable=True, index=True)
    channel_identifier = Column(String(255), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class IntegrationAPIKey(Base):
    __tablename__ = "integration_api_keys"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    key_hash = Column(String(128), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

# ============================================================
# ACCESS EVENTS
# ============================================================
class AccessEvent(Base):
   __tablename__ = "access_events"

   id = Column(Integer, primary_key=True, index=True)

   device_id = Column(String(255), nullable=True, index=True)
   reader = Column(String(50), nullable=True, index=True)
   event_type = Column(String(50), nullable=True, index=True)

   card_uid = Column(String(100), nullable=True, index=True)
   authorized = Column(Boolean, nullable=True, index=True)
   door_opened = Column(Boolean, nullable=True)

   payload = Column(JSONB, nullable=True)
   created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ============================================================
# ÍNDICES ADICIONALES (opcional pero recomendado)
# ============================================================
Index("idx_telemetry_device_time", Telemetry.device_id, Telemetry.timestamp.desc())
Index("idx_commands_device_status", Command.device_id, Command.status)
Index("idx_access_events_device_created_at", AccessEvent.device_id, AccessEvent.created_at.desc())
