import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from .models import User
from .database import engine
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET", "changeme_super_secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

pwd_context = CryptContext(
    # pbkdf2_sha256 avoids bcrypt length limits; keep bcrypt variants for legacy verification
    schemes=["pbkdf2_sha256", "bcrypt_sha256", "bcrypt"],
    default="pbkdf2_sha256",
    deprecated="auto",
    bcrypt_sha256__truncate_error=False,
    bcrypt__truncate_error=False,
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token", auto_error=False)


def verify_password(plain_password: str, hashed_password: str):
    # Legacy bcrypt hashes fail on inputs >72 bytes. Trim only for those hashes.
    is_legacy_bcrypt = hashed_password.startswith("$2")
    candidate = plain_password
    if is_legacy_bcrypt and len(candidate.encode()) > 72:
        candidate = candidate[:72]
    return pwd_context.verify(candidate, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_user_by_username(username: str, session: Session) -> Optional[User]:
    statement = select(User).where(User.username == username)
    return session.exec(statement).first()


def authenticate_user(username: str, password: str, session: Session):
    user = get_user_by_username(username, session)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def get_current_user(request: Request = None, token: Optional[str] = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    raw_token = token
    if not raw_token and request and request.cookies.get("access_token"):
        cookie_val = request.cookies.get("access_token")
        raw_token = cookie_val.split(" ", 1)[-1] if " " in cookie_val else cookie_val

    if not raw_token:
        raise credentials_exception

    try:
        payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    with Session(engine) as session:
        user = get_user_by_username(username, session)
        if user is None:
            raise credentials_exception
        return user


def get_current_user_optional(request: Request = None, token: Optional[str] = None):
    raw_token = token
    if not raw_token and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            raw_token = auth_header.split(" ", 1)[1]
        elif request.cookies.get("access_token"):
            cookie_val = request.cookies.get("access_token")
            raw_token = cookie_val.split(" ", 1)[-1] if " " in cookie_val else cookie_val

    if not raw_token:
        return None

    try:
        payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            return None
    except JWTError:
        return None

    with Session(engine) as session:
        return get_user_by_username(username, session)
