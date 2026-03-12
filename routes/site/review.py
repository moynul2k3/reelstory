from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from app.auth import login_required
from applications.site.models import SiteReview
from applications.user.models import User

router = APIRouter(prefix="/review", tags=["Site Review"])


def _serialize_review(review: SiteReview) -> Dict[str, Any]:
    return {
        "id": review.id,
        "user_id": str(review.user_id),
        "rating": review.rating,
        "comment": review.comment,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


@router.get("/my", response_model=Optional[Dict[str, Any]])
async def get_my_site_review(current_user: User = Depends(login_required)):
    review = await SiteReview.get_or_none(user=current_user)
    if not review:
        return None
    return _serialize_review(review)


@router.get("/list", response_model=List[Dict[str, Any]])
async def get_site_reviews(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    reviews = await SiteReview.all().order_by("-created_at").offset(offset).limit(limit)
    return [_serialize_review(review) for review in reviews]


@router.post("/create", response_model=Dict[str, Any])
async def create_site_review(
    rating: int = Form(...),
    comment: str = Form(...),
    current_user: User = Depends(login_required),
):
    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    existing = await SiteReview.get_or_none(user=current_user)
    if existing:
        raise HTTPException(status_code=400, detail="You have already submitted a site review")

    review = await SiteReview.create(
        user=current_user,
        rating=rating,
        comment=comment.strip(),
    )
    return _serialize_review(review)
