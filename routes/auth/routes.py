import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel

from app.token import create_access_token, create_refresh_token, get_current_user
from applications.user.models import UserRole, User
from applications.user.schemas import ensure_user_not_banned, serialize_user

router = APIRouter(tags=["Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


def build_token_payload(user: User) -> dict:
    return {
        "sub": str(user.id),
        "username": user.username or "",
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "language": user.language or "en",
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
    }


async def detect_input_type(value: str) -> str:
    email_regex = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    if re.match(email_regex, value.strip()):
        return "email"
    raise HTTPException(
        status_code=status.HTTP_406_NOT_ACCEPTABLE,
        detail="Invalid email",
    )


def _extract_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip

    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    raise HTTPException(status_code=400, detail="Unable to detect client IP")


def _extract_language_code(request: Request) -> str:
    raw_language = (
        request.headers.get("accept-language")
        or request.headers.get("x-language")
        or request.headers.get("x-lang")
        or request.headers.get("language")
        or ""
    ).strip()

    if not raw_language:
        return "en"

    language = raw_language.split(",")[0].split(";")[0].strip().lower()
    return language[:16] if language else "en"


async def _authenticate_admin(email: str, password: str) -> User:
    user = await User.get_or_none(username=email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    try:
        valid = pwd_context.verify(password, user.password)
    except Exception:
        valid = False

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.role != UserRole.ADMIN and not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


@router.post("/login_auth2/", response_model=TokenResponse)
async def login_auth2(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    await detect_input_type(form_data.username)
    user = await _authenticate_admin(form_data.username, form_data.password)
    language = _extract_language_code(request)
    update_fields = []
    if user.language != language:
        user.language = language
        update_fields.append("language")
    if update_fields:
        await user.save(update_fields=update_fields)
    token_data = build_token_payload(user)
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.post("/admin_login", response_model=TokenResponse)
async def admin_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    lookup_field = await detect_input_type(email)
    if lookup_field != "email":
        raise HTTPException(status_code=400, detail="Only email login is allowed")

    user = await _authenticate_admin(email.strip(), password)
    language = _extract_language_code(request)
    update_fields = []
    if user.language != language:
        user.language = language
        update_fields.append("language")
    if update_fields:
        await user.save(update_fields=update_fields)
    token_data = build_token_payload(user)
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.post("/login", response_model=dict)
async def login_user_by_ip(
    request: Request,
    name: Optional[str] = Form(None),
):
    username = _extract_client_ip(request)
    language = _extract_language_code(request)

    default_name = name or "Anonymous User"

    user, created = await User.get_or_create(
        username=username,
        defaults={
            "name": default_name,
            "language": language,
            "role": UserRole.USER,
        },
    )
    await ensure_user_not_banned(user)

    updated_fields = []
    if not created and name and user.name != name:
        user.name = name
        updated_fields.append("name")
    if user.language != language:
        user.language = language
        updated_fields.append("language")
    if updated_fields:
        await user.save(update_fields=updated_fields)

    token_data = build_token_payload(user)

    return {
        "message": "User login successful",
        "id": str(user.id),
        "username": user.username,
        "name": user.name,
        "language": user.language,
        "is_new": created,
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.post("/reset_password/", response_model=dict)
async def reset_password(
    user: User = Depends(get_current_user),
    password: str = Form(...),
):
    user.password = user.set_password(password)
    await user.save()

    token_data = build_token_payload(user)
    return {
        "message": "Password reset successfully",
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }



@router.get("/verify-token/")
async def verify_token(request: Request, user: User = Depends(get_current_user)):
    response_data = await serialize_user(user)
    response_data["new_tokens"] = request.state.new_tokens if hasattr(request.state, "new_tokens") else None
    return response_data
