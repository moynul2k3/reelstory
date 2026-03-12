from fastapi import APIRouter, HTTPException, status, Depends, Form
from applications.user.models import User, Permission, Group
from app.auth import *
from tortoise.contrib.pydantic import pydantic_model_creator

router = APIRouter(tags=['Permission'])

# Create Group -> superuser only
Group_Pydantic = pydantic_model_creator(Group, name="Group", exclude=[])

@router.post("/groups", response_model=Group_Pydantic, dependencies=[
    Depends(permission_required("add_group")),
])
async def create_group(
    name: str = Form(..., description="Group name"),
):
    if await Group.filter(name=name).exists():
        raise HTTPException(status_code=400, detail="Group already exists")
    group = await Group.create(name=name)
    return {"message": f"Group '{group.name}' created", "id": group.id}


# List Groups -> staff + superuser
@router.get("/groups", dependencies=[
    Depends(permission_required("view_group")),
])
async def list_groups():
    groups = await Group.all().values("id", "name")
    return groups


# Assign permissions to group -> superuser only
@router.post("/groups/{group_id}/permissions", dependencies=[
    Depends(permission_required("update_group")),
])
async def assign_permissions_to_group(
    group_id: int,
    permission_ids: list[int] = Form(..., description="List of permission IDs"),
):
    group = await Group.get_or_none(id=group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    permissions = await Permission.filter(id__in=permission_ids)
    if not permissions:
        raise HTTPException(status_code=404, detail="No valid permissions found")

    await group.permissions.add(*permissions)
    return {"message": f"Permissions assigned to group '{group.name}'"}



@router.get("/permissions", dependencies=[
    Depends(permission_required("view_permission")),
])
async def list_permissions():
    return await Permission.all().values("id", "name", "codename")

