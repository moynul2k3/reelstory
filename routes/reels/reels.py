import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Literal, Optional, Set, Tuple

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from tortoise.expressions import F
from tortoise.exceptions import OperationalError
from tortoise.functions import Avg, Count

from app.auth import permission_required
from app.config import settings
from app.utils.file_manager import compress_image_sync, delete_file
from applications.reels.category import Category
from applications.reels.reels import Reel, ReelsReview
from applications.user.models import User

router = APIRouter(prefix="/reels", tags=["Reels"])

VIDEO_EXTENSIONS: Set[str] = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp", "svg", "gif"}
COMPRESSIBLE_IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "gif"}
MAX_VIDEO_SIZE_MB = 300
MAX_IMAGE_SIZE_MB = 20

UploadPayload = Tuple[str, bytes]


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


def _media_url(relative_path: str) -> str:
    base = settings.BASE_URL.rstrip("/")
    media_root = settings.MEDIA_ROOT.strip("/")
    return f"{base}/{media_root}/{relative_path}"


def _file_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _validate_extension(filename: str, allowed_extensions: Set[str], label: str) -> str:
    ext = _file_extension(filename)
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid {label} type: {ext or 'unknown'}")
    return ext


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


async def _read_upload_bytes(file: UploadFile, max_size_mb: int, label: str) -> bytes:
    content = bytearray()
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_size_mb * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{label} exceeds {max_size_mb}MB size limit")
    return bytes(content)


async def _save_bytes_file(
    *,
    original_filename: str,
    content: bytes,
    upload_to: str,
    allowed_extensions: Set[str],
    compress_image: bool = False,
) -> str:
    ext = _file_extension(original_filename)
    if ext not in allowed_extensions:
        raise ValueError(f"Invalid file extension: {ext}")

    folder_path = os.path.join(settings.MEDIA_DIR, upload_to)
    os.makedirs(folder_path, exist_ok=True)

    if compress_image and ext in COMPRESSIBLE_IMAGE_EXTENSIONS:
        loop = asyncio.get_running_loop()
        compressed = await loop.run_in_executor(None, compress_image_sync, content)
        filename = f"{uuid.uuid4().hex}.webp"
        file_bytes = compressed
    else:
        filename = f"{uuid.uuid4().hex}.{ext}"
        file_bytes = content

    absolute_path = os.path.join(folder_path, filename)
    async with aiofiles.open(absolute_path, "wb") as stream:
        await stream.write(file_bytes)

    return _media_url(f"{upload_to}/{filename}")


async def _process_reel_uploads_in_background(
    reel_id: int,
    media_payload: Optional[UploadPayload] = None,
    thumbnail_payload: Optional[UploadPayload] = None,
    logo_payload: Optional[UploadPayload] = None,
    old_media_url: Optional[str] = None,
    old_thumbnail_url: Optional[str] = None,
    old_logo_url: Optional[str] = None,
) -> None:
    try:
        update_data = {}

        if media_payload:
            media_name, media_content = media_payload
            update_data["media_file"] = await _save_bytes_file(
                original_filename=media_name,
                content=media_content,
                upload_to="reels/videos",
                allowed_extensions=VIDEO_EXTENSIONS,
                compress_image=False,
            )

        if thumbnail_payload:
            thumb_name, thumb_content = thumbnail_payload
            update_data["thumbnail"] = await _save_bytes_file(
                original_filename=thumb_name,
                content=thumb_content,
                upload_to="reels/thumbnails",
                allowed_extensions=IMAGE_EXTENSIONS,
                compress_image=True,
            )

        if logo_payload:
            logo_name, logo_content = logo_payload
            update_data["logo"] = await _save_bytes_file(
                original_filename=logo_name,
                content=logo_content,
                upload_to="reels/logos",
                allowed_extensions=IMAGE_EXTENSIONS,
                compress_image=True,
            )

        if not update_data:
            return

        reel = await Reel.get_or_none(id=reel_id)
        if not reel:
            return

        for field, value in update_data.items():
            setattr(reel, field, value)
        await reel.save(update_fields=list(update_data.keys()))

        if "media_file" in update_data and old_media_url and old_media_url != update_data["media_file"]:
            await delete_file(old_media_url)
        if "thumbnail" in update_data and old_thumbnail_url and old_thumbnail_url != update_data["thumbnail"]:
            await delete_file(old_thumbnail_url)
        if "logo" in update_data and old_logo_url and old_logo_url != update_data["logo"]:
            await delete_file(old_logo_url)
    except Exception as error:
        print(f"[reel-upload] failed for reel_id={reel_id}: {error}")


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

    media_payload = None
    thumbnail_payload = None
    logo_payload = None

    if media_file and media_file.filename:
        _validate_extension(media_file.filename, VIDEO_EXTENSIONS, "video")
        media_payload = (
            media_file.filename,
            await _read_upload_bytes(media_file, max_size_mb=MAX_VIDEO_SIZE_MB, label="Video"),
        )

    if thumbnail and thumbnail.filename:
        _validate_extension(thumbnail.filename, IMAGE_EXTENSIONS, "thumbnail")
        thumbnail_payload = (
            thumbnail.filename,
            await _read_upload_bytes(thumbnail, max_size_mb=MAX_IMAGE_SIZE_MB, label="Thumbnail"),
        )

    if logo and logo.filename:
        _validate_extension(logo.filename, IMAGE_EXTENSIONS, "logo")
        logo_payload = (
            logo.filename,
            await _read_upload_bytes(logo, max_size_mb=MAX_IMAGE_SIZE_MB, label="Logo"),
        )

    file_upload_status = "not_provided"
    if media_payload or thumbnail_payload or logo_payload:
        background_tasks.add_task(
            _process_reel_uploads_in_background,
            reel.id,
            media_payload,
            thumbnail_payload,
            logo_payload,
            None,
            None,
            None,
        )
        file_upload_status = "processing"

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

    previous_media = reel.media_file
    previous_thumbnail = reel.thumbnail
    previous_logo = reel.logo

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

    media_payload = None
    thumbnail_payload = None
    logo_payload = None

    if media_file and media_file.filename:
        _validate_extension(media_file.filename, VIDEO_EXTENSIONS, "video")
        media_payload = (
            media_file.filename,
            await _read_upload_bytes(media_file, max_size_mb=MAX_VIDEO_SIZE_MB, label="Video"),
        )

    if thumbnail and thumbnail.filename:
        _validate_extension(thumbnail.filename, IMAGE_EXTENSIONS, "thumbnail")
        thumbnail_payload = (
            thumbnail.filename,
            await _read_upload_bytes(thumbnail, max_size_mb=MAX_IMAGE_SIZE_MB, label="Thumbnail"),
        )

    if logo and logo.filename:
        _validate_extension(logo.filename, IMAGE_EXTENSIONS, "logo")
        logo_payload = (
            logo.filename,
            await _read_upload_bytes(logo, max_size_mb=MAX_IMAGE_SIZE_MB, label="Logo"),
        )

    file_upload_status = "not_provided"
    if media_payload or thumbnail_payload or logo_payload:
        background_tasks.add_task(
            _process_reel_uploads_in_background,
            reel.id,
            media_payload,
            thumbnail_payload,
            logo_payload,
            previous_media,
            previous_thumbnail,
            previous_logo,
        )
        file_upload_status = "processing"

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
