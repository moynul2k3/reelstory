from datetime import datetime, timedelta
from enum import Enum
from typing import List, Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from tortoise.functions import Count

from applications.reels.reels import Reel, ReelsReview

router = APIRouter(prefix="/performance", tags=["Performance"])

MONTH_NAMES: tuple[str, ...] = (
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


def _to_current_tz(value: datetime, now: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=now.tzinfo)
    return value.astimezone(now.tzinfo)


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

            local_created_at = _to_current_tz(created_at, now)
            day_label = local_created_at.strftime("%A")
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

            local_created_at = _to_current_tz(created_at, now)
            month_label = MONTH_NAMES[local_created_at.month - 1]
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

            local_created_at = _to_current_tz(created_at, now)
            day = local_created_at.day
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
