from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from tortoise.transactions import in_transaction

from app.auth import login_required, permission_required
from app.utils.file_manager import delete_file, update_file
from applications.user.models import BannedType, Group, IsBanned, Permission, User, UserRole
from applications.user.schemas import get_user_ban_status, serialize_user

router = APIRouter(tags=["User"])


@router.get(
    "/users",
    dependencies=[
        Depends(login_required),
        Depends(permission_required("view_user")),
    ],
)
async def get_all_users():
    return await User.all().order_by("-created_at").values(
        "id",
        "username",
        "name",
        "language",
        "role",
        "photo",
        "is_active",
        "is_superuser",
        "created_at",
        "updated_at",
    )


@router.get("/users/{user_id}")
async def get_user(user_id: UUID, current_user: User = Depends(login_required)):
    user = await User.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if current_user.id != user.id and not await current_user.has_permission("view_user"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")

    return await serialize_user(user)


@router.put("/users/{user_id}", response_model=dict, dependencies=[Depends(login_required)])
async def update_user(
    user_id: UUID,
    username: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    role: Optional[UserRole] = Form(None),
    is_active: Optional[bool] = Form(None),
    is_superuser: Optional[bool] = Form(None),
    group_ids: Optional[List[int]] = Form(None),
    permission_ids: Optional[List[int]] = Form(None),
    photo: Optional[UploadFile] = File(None),
    current_user: User = Depends(login_required),
):
    async with in_transaction() as connection:
        user = await User.get_or_none(id=user_id).using_db(connection).prefetch_related("groups", "user_permissions")
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        has_update_permission = await current_user.has_permission("update_user")

        if current_user.id != user.id and not has_update_permission:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to update this user.")

        if user.is_superuser and not current_user.is_superuser:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot update a superuser account.")

        sensitive_fields_changed = any(
            value is not None for value in (is_active, is_superuser, role, group_ids, permission_ids)
        )
        if sensitive_fields_changed and not has_update_permission:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to update sensitive fields.")

        if username is not None:
            cleaned_username = username.strip()
            if not cleaned_username:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username cannot be empty.")
            username_exists = await User.filter(username=cleaned_username).exclude(id=user.id).using_db(connection).exists()
            if username_exists:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already in use.")
            user.username = cleaned_username

        if name is not None:
            cleaned_name = name.strip()
            user.name = cleaned_name or None

        if language is not None:
            cleaned_language = language.strip().lower()
            if not cleaned_language:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Language cannot be empty.")
            if len(cleaned_language) > 16:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Language is too long.")
            user.language = cleaned_language

        if is_active is not None:
            user.is_active = is_active

        if role is not None:
            if role == UserRole.ADMIN and not current_user.is_superuser:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superuser can assign ADMIN role.")
            user.role = role

        if is_superuser is not None:
            if not current_user.is_superuser:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only superuser can modify superuser status.",
                )
            user.is_superuser = is_superuser

        if photo is not None and photo.filename:
            user.photo = await update_file(
                photo,
                user.photo,
                upload_to="user_photo",
                allowed_extensions=["jpg", "png", "jpeg", "webp"],
            )

        await user.save(using_db=connection)

        if group_ids is not None:
            unique_group_ids = list(dict.fromkeys(group_ids))
            groups = await Group.filter(id__in=unique_group_ids).using_db(connection)
            found_group_ids = {group.id for group in groups}
            missing_group_ids = [group_id for group_id in unique_group_ids if group_id not in found_group_ids]
            if missing_group_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"message": "Some groups were not found", "missing_group_ids": missing_group_ids},
                )

            await user.groups.clear()
            if groups:
                await user.groups.add(*groups)

        if permission_ids is not None:
            unique_permission_ids = list(dict.fromkeys(permission_ids))
            permissions = await Permission.filter(id__in=unique_permission_ids).using_db(connection)
            found_permission_ids = {permission.id for permission in permissions}
            missing_permission_ids = [
                permission_id for permission_id in unique_permission_ids if permission_id not in found_permission_ids
            ]
            if missing_permission_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"message": "Some permissions were not found", "missing_permission_ids": missing_permission_ids},
                )

            await user.user_permissions.clear()
            if permissions:
                await user.user_permissions.add(*permissions)

    updated_user = await User.get(id=user_id)
    return {"message": "User updated successfully", "user": await serialize_user(updated_user)}


@router.delete(
    "/users/{user_id}",
    dependencies=[
        Depends(login_required),
        Depends(permission_required("delete_user")),
    ],
)
async def delete_user(user_id: UUID, current_user: User = Depends(login_required)):
    user = await User.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account.")

    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete a superuser account.")

    if user.photo:
        await delete_file(user.photo)

    await user.delete()
    return {"detail": "User deleted successfully"}


@router.post("/users/{user_id}/ban", dependencies=[Depends(login_required)], response_model=dict)
async def ban_user(
    user_id: UUID,
    banned_type: BannedType = Form(...),
    current_user: User = Depends(login_required),
):
    can_ban = current_user.is_superuser or current_user.role == UserRole.ADMIN or await current_user.has_permission(
        "update_user"
    )
    if not can_ban:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can ban users.")

    user = await User.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot ban your own account.")

    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot ban a superuser account.")

    ban_record, created = await IsBanned.get_or_create(
        user=user,
        defaults={"banned_type": banned_type},
    )

    if not created:
        ban_record.banned_type = banned_type
        await ban_record.save()

    ban_status = await get_user_ban_status(user)
    return {
        "message": "User banned successfully",
        "user_id": str(user.id),
        "banned_type": ban_status["banned_type"],
        "is_banned": ban_status["is_banned"],
        "due_time": ban_status["due_time"],
    }
