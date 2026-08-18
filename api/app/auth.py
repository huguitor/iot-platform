# api/app/auth.py

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .schemas import TokenData


SECRET_KEY = os.getenv("JWT_SECRET", "changeme")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "60"))
ADMIN_USER = os.getenv("API_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("API_ADMIN_PASSWORD", "admin123")

MODULE_ACCESS_FIELDS = {
    "access_control": "can_access_control",
    "monitoring": "can_access_monitoring",
}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "pbkdf2_sha256"],
    deprecated="auto",
)


# ============================================================
# PASSWORDS
# ============================================================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ============================================================
# AUTHENTICATION
# ============================================================
def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()

    if not user:
        return None

    if not user.is_active:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode = data.copy()
    to_encode.update(
        {
            "iat": int(now.timestamp()),
            "exp": expire,
        }
    )

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        user_id = payload.get("sub")
        username = payload.get("username")
        role = payload.get("role")

        if user_id is None:
            raise credentials_exception

        token_data = TokenData(
            user_id=int(user_id),
            username=username,
            role=role,
        )

    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    user = db.execute(
        select(User).where(User.id == token_data.user_id)
    ).scalar_one_or_none()

    if not user:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# ============================================================
# ROLE HELPERS
# ============================================================
def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def get_effective_module_access(user: User) -> dict:
    """Calcula permisos efectivos de entrada a los módulos funcionales."""
    if user.role in {"admin", "superadmin"}:
        return {"access_control": True, "monitoring": True}

    return {
        "access_control": bool(user.can_access_control),
        "monitoring": bool(user.can_access_monitoring),
    }


def require_module(module_name: str):
    if module_name not in MODULE_ACCESS_FIELDS:
        raise ValueError(f"Unknown module: {module_name}")

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not get_effective_module_access(current_user)[module_name]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access to module '{module_name}' is not enabled",
            )
        return current_user

    return dependency


def require_any_module(current_user: User = Depends(get_current_user)) -> User:
    if not any(get_effective_module_access(current_user).values()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No platform modules are enabled for this user",
        )
    return current_user


# ============================================================
# TOKEN RESPONSE
# ============================================================
def get_token_response(form_data: OAuth2PasswordRequestForm, db: Session):
    user = authenticate_user(db, form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token = create_access_token(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
        }
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


# ============================================================
# INITIAL ADMIN
# ============================================================
def ensure_admin_user(db: Session):
    """
    Create initial admin user from env vars if not present.
    """
    existing = db.execute(
        select(User).where(User.username == ADMIN_USER)
    ).scalar_one_or_none()

    if existing:
        return existing

    admin_user = User(
        username=ADMIN_USER,
        hashed_password=get_password_hash(ADMIN_PASSWORD),
        role="admin",
        is_active=True,
        can_access_control=True,
        can_access_monitoring=True,
    )

    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)

    return admin_user
