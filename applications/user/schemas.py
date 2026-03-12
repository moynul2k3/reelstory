from typing import Dict, Any, List

from applications.user.models import User, Permission


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


async def serialize_user(user: User) -> Dict[str, Any]:
    await user.fetch_related(
        "groups",
        "groups__permissions",
        "user_permissions",
    )

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
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,

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
