from fastapi import Depends, HTTPException, status, Request, Header
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timedelta, timezone
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()
from applications.user.models import User

# =========================
# JWT SETTINGS
# =========================
SECRET_KEY = "viB2ysUJ7a91SRDPZIHWtjIUlpH-m0Ye0dnrtzsoO1M"
REFRESH_SECRET_KEY = "IeesoMBlYQjADtCqclUXr58la1ZvlRkqnfcWUNTAn4Q"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1000000
REFRESH_TOKEN_EXPIRE_DAYS = 70


# =========================
# TOKEN HELPERS
# =========================
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, REFRESH_SECRET_KEY, algorithm=ALGORITHM)



# =========================
# AUTH HELPERS
# =========================
async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    refresh_token: str = Header(default=None, alias="refresh_token")
) -> User:
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token expired. Refresh token required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            refresh_payload = jwt.decode(refresh_token, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])

            refresh_user = await User.get_or_none(id=refresh_payload.get("sub"))
            if not refresh_user or not refresh_user.is_active:
                raise HTTPException(status_code=403, detail="Invalid or inactive user")

            token_data = {
                "sub": str(refresh_user.id),
                "username": refresh_user.username or "",
                "role": refresh_user.role.value if hasattr(refresh_user.role, "value") else str(refresh_user.role),
                "language": refresh_user.language or "en",
                "is_active": refresh_user.is_active,
                "is_superuser": refresh_user.is_superuser,
            }

            new_access_token = create_access_token(token_data)
            new_refresh_token = create_refresh_token(token_data)

            request.state.new_tokens = {
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
            }

            payload = jwt.decode(new_access_token, SECRET_KEY, algorithms=[ALGORITHM])

        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token expired. Please log in again.",
            )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await User.get_or_none(id=payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")

    return user
