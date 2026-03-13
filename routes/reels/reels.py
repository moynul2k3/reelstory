import json
from datetime import datetime, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from tortoise.expressions import F
from tortoise.exceptions import OperationalError
from tortoise.functions import Avg, Count

from app.auth import permission_required
from app.utils.file_manager import delete_file
from app.utils.reel_file_manager import prepare_reel_upload_payloads, queue_reel_upload_task
from applications.reels.category import Category
from applications.reels.reels import Reel, ReelsReview
from applications.user.models import User

router = APIRouter(prefix="/reels", tags=["Reels"])


class ReelOut(BaseModel):
    id: int
    title: str
    category_id: Optional[int] = None
    bonuses: int
    share_count: int
    view_count: int
    total_review: int
    avg_rating: Optional[float] = None
    short_description: Optional[str] = None
    terms_highlights: Optional[str] = None
    affiliate_link: Optional[str] = None
    languages: str
    tags: List[str] = Field(default_factory=list)
    disclaimers: Optional[str] = None
    media_file: Optional[str] = None
    thumbnail: Optional[str] = None
    logo: Optional[str] = None
    is_adult_content: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ReelListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[ReelOut]

class ReelViewersIn(BaseModel):
    user_ids: List[str] = Field(default_factory=list)


def _clean_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Title is required")
    return cleaned


def _parse_tags(raw_tags: Optional[str]) -> List[str]:
    if raw_tags is None:
        return []

    raw_tags = raw_tags.strip()
    if not raw_tags:
        return []

    try:
        parsed = json.loads(raw_tags)
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400, detail="tags must be a JSON array or comma-separated string")
        return [str(tag).strip() for tag in parsed if str(tag).strip()]
    except json.JSONDecodeError:
        return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]


def _serialize_reel(reel: Reel) -> ReelOut:
    tags = reel.tags if isinstance(reel.tags, list) else []
    avg_rating = getattr(reel, "avg_rating", None)
    view_count = int(getattr(reel, "view_count", 0) or 0)
    total_review = int(getattr(reel, "total_review", 0) or 0)
    if avg_rating is not None:
        avg_rating = float(avg_rating)
    return ReelOut(
        id=reel.id,
        title=reel.title,
        category_id=reel.category_id,
        bonuses=reel.bonuses,
        share_count=reel.share_count,
        view_count=view_count,
        total_review=total_review,
        avg_rating=avg_rating,
        short_description=reel.short_description,
        terms_highlights=reel.terms_highlights,
        affiliate_link=reel.affiliate_link,
        languages=reel.languages,
        tags=tags,
        disclaimers=reel.disclaimers,
        media_file=reel.media_file,
        thumbnail=reel.thumbnail,
        logo=reel.logo,
        is_adult_content=reel.is_adult_content,
        is_active=reel.is_active,
        created_at=reel.created_at,
        updated_at=reel.updated_at,
    )


async def _attach_avg_ratings(reels: List[Reel]) -> None:
    if not reels:
        return

    reel_ids = [reel.id for reel in reels]
    rating_map = {}

    try:
        rating_rows = (
            await ReelsReview.filter(
                reel_id__in=reel_ids,
                parent_id__isnull=True,
                rating__isnull=False,
            )
            .annotate(avg_rating=Avg("rating"))
            .group_by("reel_id")
            .values("reel_id", "avg_rating")
        )
        rating_map = {row["reel_id"]: float(row["avg_rating"]) for row in rating_rows if row["avg_rating"] is not None}
    except OperationalError:
        rating_map = {}

    for reel in reels:
        reel.avg_rating = rating_map.get(reel.id)


async def _attach_view_counts(reels: List[Reel]) -> None:
    if not reels:
        return

    reel_ids = [reel.id for reel in reels]
    count_map = {}

    try:
        count_rows = await Reel.filter(id__in=reel_ids).annotate(view_count=Count("viewers")).values("id", "view_count")
        count_map = {row["id"]: int(row["view_count"] or 0) for row in count_rows}
    except OperationalError:
        count_map = {}

    for reel in reels:
        reel.view_count = count_map.get(reel.id, 0)


async def _attach_review_counts(reels: List[Reel]) -> None:
    if not reels:
        return

    reel_ids = [reel.id for reel in reels]
    count_map = {}

    try:
        count_rows = (
            await ReelsReview.filter(reel_id__in=reel_ids, parent_id__isnull=True)
            .annotate(total_review=Count("id"))
            .group_by("reel_id")
            .values("reel_id", "total_review")
        )
        count_map = {row["reel_id"]: int(row["total_review"] or 0) for row in count_rows}
    except OperationalError:
        count_map = {}

    for reel in reels:
        reel.total_review = count_map.get(reel.id, 0)


@router.post(
    "/",
    response_model=dict,
    dependencies=[Depends(permission_required("add_reel"))],
)
async def create_reel(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    category_id: Optional[int] = Form(None),
    bonuses: int = Form(0),
    short_description: Optional[str] = Form(None),
    terms_highlights: Optional[str] = Form(None),
    affiliate_link: Optional[str] = Form(None),
    languages: str = Form("en"),
    tags: Optional[str] = Form(None),
    disclaimers: Optional[str] = Form(None),
    is_adult_content: bool = Form(False),
    is_active: bool = Form(True),
    media_file: Optional[UploadFile] = File(None),
    thumbnail: Optional[UploadFile] = File(None),
    logo: Optional[UploadFile] = File(None),
):
    cleaned_title = _clean_title(title)

    if category_id is not None and not await Category.filter(id=category_id).exists():
        raise HTTPException(status_code=404, detail="Category not found")

    media_payload, thumbnail_payload, logo_payload = await prepare_reel_upload_payloads(
        media_file=media_file,
        thumbnail=thumbnail,
        logo=logo,
    )

    reel = await Reel.create(
        title=cleaned_title,
        category_id=category_id,
        bonuses=bonuses,
        short_description=short_description,
        terms_highlights=terms_highlights,
        affiliate_link=affiliate_link,
        languages=languages.strip() or "en",
        tags=_parse_tags(tags),
        disclaimers=disclaimers,
        is_adult_content=is_adult_content,
        is_active=is_active,
    )

    file_upload_status = queue_reel_upload_task(
        background_tasks,
        reel_id=reel.id,
        media_payload=media_payload,
        thumbnail_payload=thumbnail_payload,
        logo_payload=logo_payload,
    )

    return {
        "message": "Reel created successfully",
        "file_upload": file_upload_status,
        "data": _serialize_reel(reel),
    }


@router.get("/", response_model=ReelListResponse)
async def list_reels(
    category_id: Optional[int] = Query(None),
    media_type: Optional[Literal["photos", "videos"]] = Query(
        None,
        description="Filter reels by media type: photos (media_file is null) or videos (media_file exists)",
    ),
    year: Optional[int] = Query(
        None,
        ge=1900,
        le=2100,
        description="Filter reels by created_at year, for example: 2025",
    ),
    date_filter: Optional[Literal["this_week", "this_month", "this_year"]] = Query(
        None,
        description="Choose one: this_week, this_month, this_year",
    ),
    most_popular: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    base_queryset = Reel.all()

    if category_id is not None:
        base_queryset = base_queryset.filter(category_id=category_id)

    if media_type == "photos":
        base_queryset = base_queryset.filter(media_file__isnull=True)
    elif media_type == "videos":
        base_queryset = base_queryset.filter(media_file__isnull=False)

    now = datetime.now().astimezone()
    if year is not None:
        year_start = datetime(year, 1, 1, tzinfo=now.tzinfo)
        next_year_start = datetime(year + 1, 1, 1, tzinfo=now.tzinfo)
        base_queryset = base_queryset.filter(created_at__gte=year_start, created_at__lt=next_year_start)

    if date_filter == "this_week":
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        base_queryset = base_queryset.filter(created_at__gte=week_start)
    elif date_filter == "this_month":
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        base_queryset = base_queryset.filter(created_at__gte=month_start)
    elif date_filter == "this_year":
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        base_queryset = base_queryset.filter(created_at__gte=year_start)

    total = await base_queryset.count()
    queryset = base_queryset

    if most_popular:
        queryset = queryset.annotate(avg_rating=Avg("reel__rating")).order_by("-avg_rating", "-share_count", "-created_at")
    else:
        queryset = queryset.order_by("-created_at")

    try:
        reels = await queryset.offset(offset).limit(limit)
    except OperationalError as error:
        if most_popular:
            raise HTTPException(
                status_code=500,
                detail="Most popular sort is unavailable because ratings schema is out of sync. Run latest migrations (ensure reelsreview.reel_id exists).",
            ) from error
        raise

    await _attach_view_counts(reels)
    await _attach_review_counts(reels)
    await _attach_avg_ratings(reels)

    return ReelListResponse(
        total=total,
        offset=offset,
        limit=limit,
        items=[_serialize_reel(reel) for reel in reels],
    )


@router.get("/{reel_id}", response_model=ReelOut)
async def get_reel(reel_id: int):
    reel = await Reel.get_or_none(id=reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")
    await _attach_view_counts([reel])
    await _attach_review_counts([reel])
    await _attach_avg_ratings([reel])
    return _serialize_reel(reel)


@router.patch(
    "/{reel_id}",
    response_model=dict,
    dependencies=[Depends(permission_required("update_reel"))],
)
async def patch_reel(
    reel_id: int,
    background_tasks: BackgroundTasks,
    title: Optional[str] = Form(None),
    category_id: Optional[int] = Form(None),
    bonuses: Optional[int] = Form(None),
    short_description: Optional[str] = Form(None),
    terms_highlights: Optional[str] = Form(None),
    affiliate_link: Optional[str] = Form(None),
    languages: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    disclaimers: Optional[str] = Form(None),
    is_adult_content: Optional[bool] = Form(None),
    is_active: Optional[bool] = Form(None),
    media_file: Optional[UploadFile] = File(None),
    thumbnail: Optional[UploadFile] = File(None),
    logo: Optional[UploadFile] = File(None),
):
    reel = await Reel.get_or_none(id=reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")

    old_media_url = reel.media_file
    old_thumbnail_url = reel.thumbnail
    old_logo_url = reel.logo

    update_data = {}
    if title is not None:
        update_data["title"] = _clean_title(title)
    if bonuses is not None:
        update_data["bonuses"] = bonuses
    if short_description is not None:
        update_data["short_description"] = short_description
    if terms_highlights is not None:
        update_data["terms_highlights"] = terms_highlights
    if affiliate_link is not None:
        update_data["affiliate_link"] = affiliate_link
    if languages is not None:
        update_data["languages"] = languages.strip() or "en"
    if tags is not None:
        update_data["tags"] = _parse_tags(tags)
    if disclaimers is not None:
        update_data["disclaimers"] = disclaimers
    if is_adult_content is not None:
        update_data["is_adult_content"] = is_adult_content
    if is_active is not None:
        update_data["is_active"] = is_active

    if category_id is not None:
        if not await Category.filter(id=category_id).exists():
            raise HTTPException(status_code=404, detail="Category not found")
        update_data["category_id"] = category_id

    if update_data:
        await Reel.filter(id=reel_id).update(**update_data)
        reel = await Reel.get(id=reel_id)

    media_payload, thumbnail_payload, logo_payload = await prepare_reel_upload_payloads(
        media_file=media_file,
        thumbnail=thumbnail,
        logo=logo,
    )

    file_upload_status = queue_reel_upload_task(
        background_tasks,
        reel_id=reel.id,
        media_payload=media_payload,
        thumbnail_payload=thumbnail_payload,
        logo_payload=logo_payload,
        old_media_url=old_media_url,
        old_thumbnail_url=old_thumbnail_url,
        old_logo_url=old_logo_url,
    )

    await _attach_view_counts([reel])
    await _attach_review_counts([reel])

    return {
        "message": "Reel updated successfully",
        "file_upload": file_upload_status,
        "data": _serialize_reel(reel),
    }


@router.delete(
    "/{reel_id}",
    response_model=dict,
    dependencies=[Depends(permission_required("delete_reel"))],
)
async def delete_reel(reel_id: int):
    reel = await Reel.get_or_none(id=reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")

    if reel.media_file:
        await delete_file(reel.media_file)
    if reel.thumbnail:
        await delete_file(reel.thumbnail)
    if reel.logo:
        await delete_file(reel.logo)

    await reel.delete()
    return {"detail": "Reel deleted successfully"}


@router.post("/{reel_id}/share", response_model=dict)
async def increment_share_count(reel_id: int):
    updated_rows = await Reel.filter(id=reel_id).update(share_count=F("share_count") + 1)
    if not updated_rows:
        raise HTTPException(status_code=404, detail="Reel not found")

    reel = await Reel.get(id=reel_id)
    return {
        "detail": "Share count incremented successfully",
        "reel_id": reel.id,
        "share_count": reel.share_count,
    }


@router.post(
    "/{reel_id}/viewers",
    response_model=dict,
    dependencies=[Depends(permission_required("update_reel"))],
)
async def add_reel_viewers(
    reel_id: int,
    payload: ReelViewersIn,
):
    reel = await Reel.get_or_none(id=reel_id)
    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")

    normalized_ids: List[str] = []
    for raw_id in payload.user_ids:
        cleaned = str(raw_id).strip()
        if cleaned and cleaned not in normalized_ids:
            normalized_ids.append(cleaned)

    if not normalized_ids:
        raise HTTPException(status_code=400, detail="At least one valid user_id is required")

    users = await User.filter(id__in=normalized_ids)
    found_ids = {str(user.id) for user in users}
    missing_ids = [user_id for user_id in normalized_ids if user_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail={"message": "Some users were not found", "missing_user_ids": missing_ids},
        )

    await reel.viewers.add(*users)
    viewer_count = await reel.viewers.all().count()

    return {
        "detail": "Viewers added successfully",
        "reel_id": reel.id,
        "added_user_ids": sorted(found_ids),
        "viewer_count": viewer_count,
    }
