from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    status = Column(String, default="offline")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True))

    telemetry = relationship("Telemetry", back_populates="device")
    commands = relationship("Command", back_populates="device")
    permissions = relationship("UserDevicePermission", back_populates="device")


class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("devices.device_id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSONB, nullable=False)

    device = relationship("Device", back_populates="telemetry")


class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("devices.device_id"), nullable=False, index=True)
    command = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    device = relationship("Device", back_populates="commands")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="user")  # "admin" or "user"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    permissions = relationship("UserDevicePermission", back_populates="user", cascade="all, delete-orphan")


class UserDevicePermission(Base):
    __tablename__ = "user_device_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String, ForeignKey("devices.device_id"), nullable=False, index=True)
    can_read = Column(Boolean, default=False, nullable=False)
    can_write = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="permissions")
    device = relationship("Device", back_populates="permissions")
