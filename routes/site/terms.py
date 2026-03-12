from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException

from app.auth import role_required
from applications.site.models import Terms
from applications.user.models import UserRole

router = APIRouter(prefix="/terms", tags=["Terms Info"])


def _serialize_terms(terms: Terms) -> dict:
    return {
        "id": terms.id,
        "title": terms.title,
        "details": terms.details,
        "updated_at": terms.updated_at,
    }


@router.get("/", response_model=List[dict])
async def get_terms():
    terms_rows = await Terms.all().order_by("-updated_at")
    return [_serialize_terms(item) for item in terms_rows]


@router.post("/", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def create_or_update_terms(
    title: str = Form(...),
    details: str = Form(""),
):
    terms = await Terms.get_or_none(title=title.strip())

    if terms:
        terms.details = details
    else:
        terms = await Terms.create(title=title.strip(), details=details)
    await terms.save()
    return _serialize_terms(terms)


@router.patch("/{terms_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def patch_terms(
    terms_id: int,
    title: Optional[str] = Form(None),
    details: Optional[str] = Form(None),
):
    terms = await Terms.get_or_none(id=terms_id)
    if not terms:
        raise HTTPException(status_code=404, detail="Terms entry not found")

    if title is not None and title.strip():
        terms.title = title.strip()
    if details is not None:
        terms.details = details

    await terms.save()
    return _serialize_terms(terms)


@router.delete("/{terms_id}", response_model=dict, dependencies=[Depends(role_required(UserRole("ADMIN")))])
async def delete_terms(terms_id: int):
    terms = await Terms.get_or_none(id=terms_id)
    if not terms:
        raise HTTPException(status_code=404, detail="Terms entry not found")
    await terms.delete()
    return {"status": "success", "message": "Terms deleted"}
