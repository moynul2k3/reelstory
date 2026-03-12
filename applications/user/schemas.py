from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from fastapi import HTTPException, status

from applications.user.models import User, Permission, IsBanned, BannedType


async def _resolve_optional_relation(user: User, relation_name: str):
    try:
        relation = getattr(user, relation_name)
    except Exception:
        return None

    if relation is None:
        return None

    get_or_none = getattr(relation, "get_or_none", None)
    if callable(get_or_none):
        try:
            return await get_or_none()
        except Exception:
            return None

    return relation


async def _resolve_many_relation(user: User, relation_name: str) -> List[Any]:
    try:
        relation = getattr(user, relation_name)
    except Exception:
        return []

    if relation is None:
        return []

    all_method = getattr(relation, "all", None)
    if callable(all_method):
        try:
            return await all_method()
        except Exception:
            return []

    if isinstance(relation, list):
        return relation

    try:
        return list(relation)
    except Exception:
        return []


async def _serialize_optional_profile(serializer_cls, relation_obj):
    if relation_obj is None:
        return None
    try:
        return await serializer_cls.from_tortoise_orm(relation_obj)
    except Exception:
        return None


def _ban_due_time(ban_record: IsBanned):
    banned_at = ban_record.banned_at
    if banned_at is None:
        return None
    if banned_at.tzinfo is None:
        banned_at = banned_at.replace(tzinfo=timezone.utc)

    if ban_record.banned_type == BannedType.HOURS24:
        return banned_at + timedelta(hours=24)
    if ban_record.banned_type == BannedType.DAYS7:
        return banned_at + timedelta(days=7)
    return None


async def get_user_ban_status(user: User) -> Dict[str, Any]:
    ban_record = await IsBanned.get_or_none(user_id=user.id)
    if not ban_record:
        return {"is_banned": False, "banned_type": None, "due_time": None}

    if ban_record.banned_type == BannedType.PERMANENT:
        return {"is_banned": True, "banned_type": ban_record.banned_type.value, "due_time": None}

    due_time = _ban_due_time(ban_record)
    if due_time is None:
        return {"is_banned": False, "banned_type": None, "due_time": None}

    now_utc = datetime.now(timezone.utc)
    is_banned = now_utc < due_time
    return {
        "is_banned": is_banned,
        "banned_type": ban_record.banned_type.value if is_banned else None,
        "due_time": due_time.isoformat(),
    }


async def ensure_user_not_banned(user: User) -> None:
    ban_status = await get_user_ban_status(user)
    if not ban_status["is_banned"]:
        return

    message = (
        "User is permanently banned."
        if ban_status["banned_type"] == BannedType.PERMANENT.value
        else "User is temporarily banned."
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": message,
            "banned_type": ban_status["banned_type"],
            "due_time": ban_status["due_time"],
        },
    )


async def serialize_user(user: User) -> Dict[str, Any]:
    await user.fetch_related(
        "groups",
        "groups__permissions",
        "user_permissions",
    )
    ban_status = await get_user_ban_status(user)


    if user.is_superuser:
        all_codes = await Permission.all().values_list("codename", flat=True)
        permission_codes = {code for code in all_codes if code}
    else:
        permission_codes = {p.codename for p in user.user_permissions if p.codename}
        for group in user.groups:
            for permission in group.permissions:
                if permission.codename:
                    permission_codes.add(permission.codename)



    # ---------------- RESPONSE ----------------
    return {
        # -------- BASIC INFO --------
        "id": user.id,
        "username": user.username,

        # -------- STATUS FLAGS --------
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "is_banned": ban_status["is_banned"],
        "banned_type": ban_status["banned_type"],
        "due_time": ban_status["due_time"],

        # -------- PROFILE INFO --------
        "name": user.name,
        "language": user.language,
        "photo": user.photo,

        # -------- RELATIONSHIPS --------
        "groups": [{"id": g.id, "name": g.name} for g in user.groups],
        "permissions": [
            {"id": p.id, "codename": p.codename, "name": p.name}
            for p in user.user_permissions
        ],
        "permission_codes": sorted(permission_codes),


        # -------- META --------
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }
