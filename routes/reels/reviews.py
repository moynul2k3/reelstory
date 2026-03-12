from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status

from app.auth import login_required
from applications.reels.reels import Reel, ReelsReview
from applications.user.models import User, UserRole

router = APIRouter(prefix="/reviews", tags=["Reel Reviews"])


def _clean_review_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    cleaned = text.strip()
    return cleaned if cleaned else None


def _validate_rating(rating: Optional[int]) -> None:
    if rating is None:
        return
    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")


def _can_moderate_reviews(user: User) -> bool:
    return bool(user.is_superuser or user.role == UserRole.ADMIN)


def _serialize_user(user: User) -> Dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "name": user.name,
        "photo": user.photo,
    }


async def _serialize_review_tree(review: ReelsReview) -> Dict[str, Any]:
    await review.fetch_related("user")
    children = await (
        ReelsReview.filter(parent_id=review.id)
        .prefetch_related("user")
        .order_by("created_at")
    )

    serialized_children = [await _serialize_review_tree(child) for child in children]

    return {
        "id": review.id,
        "reel_id": review.reel_id,
        "parent_id": review.parent_id,
        "rating": review.rating,
        "review": review.review,
        "is_reply": review.parent_id is not None,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
        "user": _serialize_user(review.user),
        "replies": serialized_children,
    }


@router.post("/", response_model=dict)
async def create_review(
    reel_id: int = Form(...),
    rating: Optional[int] = Form(None),
    review: Optional[str] = Form(None),
    parent_id: Optional[int] = Form(None),
    current_user: User = Depends(login_required),
):
    reel = await Reel.get_or_none(id=reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")

    cleaned_text = _clean_review_text(review)
    _validate_rating(rating)

    if parent_id is not None:
        parent_review = await ReelsReview.get_or_none(id=parent_id, reel_id=reel_id)
        if not parent_review:
            raise HTTPException(status_code=404, detail="Parent review not found")

        if rating is not None:
            raise HTTPException(status_code=400, detail="Replies cannot include rating")
        if cleaned_text is None:
            raise HTTPException(status_code=400, detail="Reply text is required")
    else:
        if rating is None:
            raise HTTPException(status_code=400, detail="Rating is required for a review")

        exists = await ReelsReview.filter(
            reel_id=reel_id,
            user_id=current_user.id,
            parent_id__isnull=True,
        ).exists()
        if exists:
            raise HTTPException(status_code=400, detail="You have already reviewed this reel")

    if rating is None and cleaned_text is None:
        raise HTTPException(status_code=400, detail="Review cannot be empty")

    created = await ReelsReview.create(
        reel_id=reel_id,
        user_id=current_user.id,
        rating=rating,
        review=cleaned_text,
        parent_id=parent_id,
    )

    return await _serialize_review_tree(created)


@router.get("/", response_model=dict)
async def list_reviews(
    reel_id: int = Query(..., ge=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    reel_exists = await Reel.filter(id=reel_id).exists()
    if not reel_exists:
        raise HTTPException(status_code=404, detail="Reel not found")

    base_query = ReelsReview.filter(reel_id=reel_id, parent_id__isnull=True)
    total = await base_query.count()
    reviews = await base_query.order_by("-created_at").offset(offset).limit(limit).prefetch_related("user")

    items = [await _serialize_review_tree(review) for review in reviews]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@router.get("/{review_id}", response_model=dict)
async def get_review(review_id: int):
    review = await ReelsReview.get_or_none(id=review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return await _serialize_review_tree(review)


@router.patch("/{review_id}", response_model=dict)
async def update_review(
    review_id: int,
    rating: Optional[int] = Form(None),
    review: Optional[str] = Form(None),
    current_user: User = Depends(login_required),
):
    review_obj = await ReelsReview.get_or_none(id=review_id, user_id=current_user.id)
    if not review_obj:
        raise HTTPException(status_code=404, detail="Review not found")

    if review_obj.user_id != current_user.id and not _can_moderate_reviews(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to update this review",
        )

    if rating is None and review is None:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    _validate_rating(rating)
    cleaned_text = _clean_review_text(review)

    if review_obj.parent_id is not None and rating is not None:
        raise HTTPException(status_code=400, detail="Replies cannot include rating")

    new_rating = rating if rating is not None else review_obj.rating
    new_review_text = cleaned_text if review is not None else review_obj.review

    if review_obj.parent_id is not None and new_review_text is None:
        raise HTTPException(status_code=400, detail="Reply text is required")
    if review_obj.parent_id is None and new_rating is None and new_review_text is None:
        raise HTTPException(status_code=400, detail="Review cannot be empty")

    update_data: Dict[str, Any] = {}
    if rating is not None:
        update_data["rating"] = rating
    if review is not None:
        update_data["review"] = cleaned_text

    if update_data:
        await ReelsReview.filter(id=review_id).update(**update_data)

    updated_review = await ReelsReview.get(id=review_id)
    return await _serialize_review_tree(updated_review)


@router.delete("/{review_id}", response_model=dict)
async def delete_review(
    review_id: int,
    current_user: User = Depends(login_required),
):
    review = await ReelsReview.get_or_none(id=review_id, user_id=current_user.id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.user_id != current_user.id and not _can_moderate_reviews(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to delete this review",
        )

    await review.delete()
    return {"detail": "Review deleted successfully"}
