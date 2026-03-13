import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
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

router = APIRouter(prefix="/performance", tags=["Performance"])

VIDEO_EXTENSIONS: Set[str] = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp", "svg", "gif"}
COMPRESSIBLE_IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "gif"}
MONTH_NAMES: Tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
LANGUAGE_SPLIT_PATTERN = re.compile(r"[,\|;/]+")
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


class ReelTypePerformance(BaseModel):
    reels: int = 0
    viewers: int = 0


class ReelPerformanceBucket(BaseModel):
    label: str
    photos: ReelTypePerformance
    videos: ReelTypePerformance


class ReelPerformanceTotals(BaseModel):
    photos: ReelTypePerformance
    videos: ReelTypePerformance
    combined_reels: int
    combined_viewers: int
    total_published_items: int
    total_performance_reviews: int
    total_engagement_reviews: int
    estimated_clicks_ctr: int


class ReelPerformanceSummary(BaseModel):
    mode: Literal["weekly", "yearly", "monthly"]
    period: str
    buckets: List[ReelPerformanceBucket]
    totals: ReelPerformanceTotals


class LanguageReelSummaryItem(BaseModel):
    language_code: str
    language_name: str
    total_reels: int = 0
    photo_reels: int = 0
    video_reels: int = 0
    total_views: int = 0
    total_reviews: int = 0
    engagement_reviews: int = 0


class LanguageReelSummaryResponse(BaseModel):
    total_languages: int
    total_languages_with_reels: int
    items: List[LanguageReelSummaryItem]


class MonthName(str, Enum):
    JANUARY = "January"
    FEBRUARY = "February"
    MARCH = "March"
    APRIL = "April"
    MAY = "May"
    JUNE = "June"
    JULY = "July"
    AUGUST = "August"
    SEPTEMBER = "September"
    OCTOBER = "October"
    NOVEMBER = "November"
    DECEMBER = "December"


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


def _build_world_language_maps() -> Tuple[dict[str, str], dict[str, str]]:
    code_to_name: dict[str, str] = {}
    name_to_code: dict[str, str] = {}

    try:
        from babel import Locale

        raw_languages = dict(Locale("en").languages)
    except Exception:
        raw_languages = {}

    for raw_code, raw_name in raw_languages.items():
        if not isinstance(raw_code, str) or not isinstance(raw_name, str):
            continue

        code = raw_code.strip().lower()
        name = raw_name.strip()
        if not code or not name:
            continue

        primary_code = code.split("-")[0].split("_")[0]
        if not primary_code.isalpha() or len(primary_code) not in (2, 3):
            continue

        if primary_code not in code_to_name:
            code_to_name[primary_code] = name
        if name.lower() not in name_to_code:
            name_to_code[name.lower()] = primary_code

    if "en" not in code_to_name:
        code_to_name["en"] = "English"
    if "english" not in name_to_code:
        name_to_code["english"] = "en"

    ordered_code_to_name = dict(sorted(code_to_name.items(), key=lambda item: item[1].lower()))
    return ordered_code_to_name, name_to_code


def _parse_reel_languages(
    raw_languages: Optional[str],
    language_code_to_name: dict[str, str],
    language_name_to_code: dict[str, str],
) -> List[str]:
    if not raw_languages:
        return []

    normalized_languages: List[str] = []
    for chunk in LANGUAGE_SPLIT_PATTERN.split(raw_languages):
        cleaned = chunk.strip()
        if not cleaned:
            continue

        lowered = cleaned.lower()
        primary = lowered.split("-")[0].split("_")[0]
        language_code = None

        if primary in language_code_to_name:
            language_code = primary
        elif lowered in language_name_to_code:
            language_code = language_name_to_code[lowered]
        elif primary in language_name_to_code:
            language_code = language_name_to_code[primary]
        elif primary.isalpha() and len(primary) in (2, 3):
            language_code = primary
            if language_code not in language_code_to_name:
                language_code_to_name[language_code] = cleaned.title()

        if language_code and language_code not in normalized_languages:
            normalized_languages.append(language_code)

    return normalized_languages


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
    if avg_rating is not None:
        avg_rating = float(avg_rating)
    return ReelOut(
        id=reel.id,
        title=reel.title,
        category_id=reel.category_id,
        bonuses=reel.bonuses,
        share_count=reel.share_count,
        view_count=view_count,
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



@router.get("/performance-summary", response_model=ReelPerformanceSummary)
async def reels_performance_summary(
    mode: Literal["weekly", "yearly", "monthly"] = Query(
        ...,
        description="Summary mode: weekly (Sunday-Friday), monthly (5-day buckets), or yearly (month-wise)",
    ),
    year: Optional[int] = Query(
        None,
        ge=1900,
        le=2100,
        description="Target year for yearly/monthly mode. Defaults to current year.",
    ),
    month: Optional[MonthName] = Query(
        None,
        description="Target month for monthly mode. Select from dropdown. Defaults to current month.",
    ),
):
    now = datetime.now().astimezone()

    if mode == "weekly":
        labels = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        bucket_map = {
            label: {"photos": {"reels": 0, "viewers": 0}, "videos": {"reels": 0, "viewers": 0}}
            for label in labels
        }
        days_since_sunday = (now.weekday() + 1) % 7
        week_start = (now - timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        rows = (
            await Reel.filter(created_at__gte=week_start, created_at__lt=week_end)
            .annotate(view_count=Count("viewers"))
            .values("id", "created_at", "media_file", "view_count")
        )

        for row in rows:
            created_at = row["created_at"]
            if not created_at:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=now.tzinfo)
            else:
                created_at = created_at.astimezone(now.tzinfo)

            day_label = created_at.strftime("%A")
            if day_label not in bucket_map:
                continue

            media_key = "videos" if row["media_file"] else "photos"
            bucket_map[day_label][media_key]["reels"] += 1
            bucket_map[day_label][media_key]["viewers"] += int(row["view_count"] or 0)

        period = f"{week_start.date().isoformat()} to {(week_end - timedelta(days=1)).date().isoformat()}"
    elif mode == "yearly":
        target_year = year or now.year
        year_start = datetime(target_year, 1, 1, tzinfo=now.tzinfo)
        next_year_start = datetime(target_year + 1, 1, 1, tzinfo=now.tzinfo)
        labels = list(MONTH_NAMES)
        bucket_map = {
            label: {"photos": {"reels": 0, "viewers": 0}, "videos": {"reels": 0, "viewers": 0}}
            for label in labels
        }
        rows = (
            await Reel.filter(created_at__gte=year_start, created_at__lt=next_year_start)
            .annotate(view_count=Count("viewers"))
            .values("id", "created_at", "media_file", "view_count")
        )

        for row in rows:
            created_at = row["created_at"]
            if not created_at:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=now.tzinfo)
            else:
                created_at = created_at.astimezone(now.tzinfo)

            month_label = MONTH_NAMES[created_at.month - 1]
            media_key = "videos" if row["media_file"] else "photos"
            bucket_map[month_label][media_key]["reels"] += 1
            bucket_map[month_label][media_key]["viewers"] += int(row["view_count"] or 0)

        period = str(target_year)
    else:
        target_year = year or now.year
        target_month = MONTH_NAMES.index(month.value) + 1 if month else now.month
        month_start = datetime(target_year, target_month, 1, tzinfo=now.tzinfo)
        if target_month == 12:
            next_month_start = datetime(target_year + 1, 1, 1, tzinfo=now.tzinfo)
        else:
            next_month_start = datetime(target_year, target_month + 1, 1, tzinfo=now.tzinfo)

        month_day_count = (next_month_start - month_start).days
        month_name = MONTH_NAMES[target_month - 1]
        labels = []
        bucket_map = {}

        day_start = 1
        while day_start <= month_day_count:
            day_end = min(day_start + 4, month_day_count)
            label = f"{day_start:02d}-{day_end:02d} {month_name}"
            labels.append(label)
            bucket_map[label] = {"photos": {"reels": 0, "viewers": 0}, "videos": {"reels": 0, "viewers": 0}}
            day_start += 5

        rows = (
            await Reel.filter(created_at__gte=month_start, created_at__lt=next_month_start)
            .annotate(view_count=Count("viewers"))
            .values("id", "created_at", "media_file", "view_count")
        )

        for row in rows:
            created_at = row["created_at"]
            if not created_at:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=now.tzinfo)
            else:
                created_at = created_at.astimezone(now.tzinfo)

            day = created_at.day
            bucket_start = ((day - 1) // 5) * 5 + 1
            bucket_end = min(bucket_start + 4, month_day_count)
            bucket_label = f"{bucket_start:02d}-{bucket_end:02d} {month_name}"

            media_key = "videos" if row["media_file"] else "photos"
            bucket_map[bucket_label][media_key]["reels"] += 1
            bucket_map[bucket_label][media_key]["viewers"] += int(row["view_count"] or 0)

        period = f"{month_start.date().isoformat()} to {(next_month_start - timedelta(days=1)).date().isoformat()}"

    buckets = [
        ReelPerformanceBucket(
            label=label,
            photos=ReelTypePerformance(**bucket_map[label]["photos"]),
            videos=ReelTypePerformance(**bucket_map[label]["videos"]),
        )
        for label in labels
    ]

    photos_reels = sum(bucket.photos.reels for bucket in buckets)
    photos_viewers = sum(bucket.photos.viewers for bucket in buckets)
    videos_reels = sum(bucket.videos.reels for bucket in buckets)
    videos_viewers = sum(bucket.videos.viewers for bucket in buckets)
    combined_reels = photos_reels + videos_reels
    combined_viewers = photos_viewers + videos_viewers

    reel_ids = list({int(row["id"]) for row in rows if row.get("id") is not None})
    total_published_items = len(reel_ids)
    total_performance_reviews = 0
    total_engagement_reviews = 0
    if reel_ids:
        total_performance_reviews = await ReelsReview.filter(reel_id__in=reel_ids).count()
        total_engagement_reviews = (
            await ReelsReview.filter(reel_id__in=reel_ids, review__isnull=False).exclude(review="").count()
        )

    totals = ReelPerformanceTotals(
        photos=ReelTypePerformance(reels=photos_reels, viewers=photos_viewers),
        videos=ReelTypePerformance(reels=videos_reels, viewers=videos_viewers),
        combined_reels=combined_reels,
        combined_viewers=combined_viewers,
        total_published_items=total_published_items,
        total_performance_reviews=total_performance_reviews,
        total_engagement_reviews=total_engagement_reviews,
        estimated_clicks_ctr=combined_viewers,
    )

    return ReelPerformanceSummary(
        mode=mode,
        period=period,
        buckets=buckets,
        totals=totals,
    )
