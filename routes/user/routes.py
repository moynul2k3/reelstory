from fastapi import APIRouter, HTTPException, status, Depends, Form, Query, Body, UploadFile, File
from typing import List, Optional
from app.auth import login_required, permission_required
from applications.user.models import User, Permission, Group, IsBanned, BannedType, UserRole
from applications.user.schemas import get_user_ban_status
from app.utils.otp_manager import verify_otp
from app.utils.file_manager import update_file, delete_file
from tortoise.transactions import in_transaction
from app.utils.phone_number import phone_number

from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(tags=['User'])



@router.get("/users", dependencies=[
        Depends(login_required),
        Depends(permission_required("view_user")),
    ]
)
async def get_all_users():
    return await User.all().values(
        "id", "phone", "email", "is_active", "is_rider", "is_vendor", "is_superuser", "created_at", "updated_at"
    )


@router.get(
    "/users/{user_id}",
    dependencies=[
        Depends(login_required),
    ]
)
async def get_user(user_id: int, current_user: User = Depends(login_required)):
    user = await User.get_or_none(id=user_id).prefetch_related("groups", "user_permissions")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.is_rider or current_user.is_vendor:
        if current_user.id != user.id:
            return {
                "id": user.id,
                "phone": user.phone,
                "email": user.email,
                "created_at": user.created_at,
            }

    groups = [group.name for group in await user.groups.all()]
    user_perms = [perm.codename for perm in await user.user_permissions.all()]

    group_perms = []
    if current_user.is_superuser or current_user.id == user.id:
        for group in await user.groups.all():
            perms = await group.permissions.all()
            group_perms.extend([perm.codename for perm in perms])

    all_perms = list(set(user_perms + group_perms))

    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "groups": groups,
        "permissions": all_perms,
    }


@router.put("/{user_id}", dependencies=[Depends(login_required)], response_model=dict)
async def update_user(
    user_id: int,
    phone: Optional[str] = Form(None),
    otp: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
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
            raise HTTPException(status_code=404, detail="User not found")

        # Permission checks
        if user.is_superuser and not current_user.is_superuser:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot update a superuser account.")

        if current_user.id != user.id:
            has_perm = await current_user.has_permission("update_user")
            if not has_perm:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to update this user.")

        sensitive_fields = [is_active, is_superuser, group_ids, permission_ids]
        if any(v is not None for v in sensitive_fields):
            has_perm = await current_user.has_permission("update_user")
            if not has_perm:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to update sensitive fields.")

        # Email
        if email:
            email_exists = await User.filter(email=email).exclude(id=user.id).using_db(connection).exists()
            if email_exists:
                raise HTTPException(status_code=400, detail="Email already in use.")
            user.email = email

        # Phone update with OTP verification
        if phone:
            phone = await phone_number(phone)
            if not otp:
                raise HTTPException(status_code=400, detail="OTP is required to update phone number.")
            verified = await verify_otp(phone, otp, purpose="update_user_data")
            if not verified:
                raise HTTPException(status_code=400, detail="OTP not verified.")
            user.phone = phone

        # Update flags
        if is_active is not None:
            user.is_active = is_active
        if is_superuser is not None:
            if not current_user.is_superuser:
                raise HTTPException(status_code=403, detail="Only superuser can modify superuser status.")
            user.is_superuser = is_superuser

        # Photo update
        if photo is not None:
            user.photo = await update_file(photo, user.photo, upload_to="user_photo", allowed_extensions=["jpg","png","jpeg","webp"])

        await user.save(using_db=connection)

        # Groups
        if group_ids is not None:
            groups = await Group.filter(id__in=group_ids).using_db(connection)
            await user.groups.clear()
            await user.groups.add(*groups)

        # Permissions
        if permission_ids is not None:
            permissions = await Permission.filter(id__in=permission_ids).using_db(connection)
            await user.user_permissions.clear()
            await user.user_permissions.add(*permissions)

    # Prepare response
    user_data = {
        "id": user.id,
        "phone": user.phone,
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "groups": [g.id for g in await user.groups.all()],
        "permissions": [p.id for p in await user.user_permissions.all()],
    }

    return {"message": "User updated successfully", "user": user_data}



@router.delete("/users/{user_id}", dependencies=[
        Depends(login_required),
        Depends(permission_required("delete_user")),
    ]
)
async def delete_user(user_id: int):
    user = await User.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await delete_file(user.photo)
    await user.delete()
    return {"detail": "User deleted successfully"}


@router.post("/users/{user_id}/ban", dependencies=[Depends(login_required)], response_model=dict)
async def ban_user(
    user_id: str,
    banned_type: BannedType = Form(...),
    current_user: User = Depends(login_required),
):
    if current_user.role != UserRole.ADMIN and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can ban users.")

    user = await User.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot ban your own account.")

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




