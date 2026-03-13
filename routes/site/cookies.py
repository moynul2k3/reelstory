from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException

from app.auth import role_required
from applications.site.models import CookiesPolicy
from applications.user.models import UserRole

router = APIRouter(prefix="/cookies", tags=["Cookies Policy Info"])


def _serialize_cookies_policy(cookies_policy: CookiesPolicy) -> dict:
    return {
        "id": cookies_policy.id,
        "title": cookies_policy.title,
        "details": cookies_policy.details,
        "updated_at": cookies_policy.updated_at,
    }


@router.get("/", response_model=List[dict])
async def get_cookies_policies():
    cookies_rows = await CookiesPolicy.all().order_by("-updated_at")
    return [_serialize_cookies_policy(item) for item in cookies_rows]


@router.post("/", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def create_or_update_cookies_policy(
    title: str = Form(...),
    details: str = Form(""),
):
    cookies_policy = await CookiesPolicy.get_or_none(title=title.strip())

    if cookies_policy:
        cookies_policy.details = details
    else:
        cookies_policy = await CookiesPolicy.create(title=title.strip(), details=details)

    await cookies_policy.save()
    return _serialize_cookies_policy(cookies_policy)


@router.patch("/{cookies_policy_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def patch_cookies_policy(
    cookies_policy_id: int,
    title: Optional[str] = Form(None),
    details: Optional[str] = Form(None),
):
    cookies_policy = await CookiesPolicy.get_or_none(id=cookies_policy_id)
    if not cookies_policy:
        raise HTTPException(status_code=404, detail="Cookies policy entry not found")

    if title is not None and title.strip():
        cookies_policy.title = title.strip()
    if details is not None:
        cookies_policy.details = details

    await cookies_policy.save()
    return _serialize_cookies_policy(cookies_policy)


@router.delete("/{cookies_policy_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def delete_cookies_policy(cookies_policy_id: int):
    cookies_policy = await CookiesPolicy.get_or_none(id=cookies_policy_id)
    if not cookies_policy:
        raise HTTPException(status_code=404, detail="Cookies policy entry not found")

    await cookies_policy.delete()
    return {"status": "success", "message": "Cookies policy deleted"}
