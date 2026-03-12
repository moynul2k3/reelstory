from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException

from app.auth import role_required
from applications.site.models import Policy
from applications.user.models import UserRole

router = APIRouter(prefix="/privacy", tags=["Policy Info"])


def _serialize_policy(policy: Policy) -> dict:
    return {
        "id": policy.id,
        "title": policy.title,
        "details": policy.details,
        "updated_at": policy.updated_at,
    }


@router.get("/", response_model=List[dict])
async def get_policies():
    return [_serialize_policy(item) for item in await Policy.all().order_by("-updated_at")]


@router.post("/", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def create_or_update_policy(
    title: str = Form(...),
    details: str = Form(""),
):
    policy = await Policy.get_or_none(title=title.strip())
    if policy:
        policy.details = details
    else:
        policy = await Policy.create(title=title.strip(), details=details)

    await policy.save()
    return _serialize_policy(policy)


@router.patch("/{policy_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def patch_policy(
    policy_id: int,
    title: Optional[str] = Form(None),
    details: Optional[str] = Form(None),
):
    policy = await Policy.get_or_none(id=policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy entry not found")

    if title is not None and title.strip():
        policy.title = title.strip()
    if details is not None:
        policy.details = details

    await policy.save()
    return _serialize_policy(policy)


@router.delete("/{policy_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def delete_policy(policy_id: int):
    policy = await Policy.get_or_none(id=policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy entry not found")
    await policy.delete()
    return {"status": "success", "message": "Policy deleted"}
