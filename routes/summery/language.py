import re
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from tortoise.functions import Count

from applications.reels.reels import Reel, ReelsReview

router = APIRouter(prefix="/language", tags=["Language"])

LANGUAGE_SPLIT_PATTERN = re.compile(r"[,\|;/]+")


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


def _build_world_language_maps() -> tuple[dict[str, str], dict[str, str]]:
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


@router.get("/language-summary", response_model=LanguageReelSummaryResponse)
async def reels_language_summary(
    include_empty: bool = Query(
        True,
        description="Include languages with zero reels in the response",
    ),
):
    language_code_to_name, language_name_to_code = _build_world_language_maps()
    rows = await Reel.all().annotate(view_count=Count("viewers")).values("id", "languages", "media_file", "view_count")

    empty_stats = {
        "total_reels": 0,
        "photo_reels": 0,
        "video_reels": 0,
        "total_views": 0,
        "total_reviews": 0,
        "engagement_reviews": 0,
    }
    stats: dict[str, dict[str, int]] = {code: dict(empty_stats) for code in language_code_to_name.keys()}

    reel_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    review_totals_by_reel: dict[int, int] = {}
    engagement_totals_by_reel: dict[int, int] = {}
    if reel_ids:
        review_rows = await ReelsReview.filter(reel_id__in=reel_ids).values("reel_id", "review")
        for review_row in review_rows:
            reel_id = int(review_row["reel_id"])
            review_totals_by_reel[reel_id] = review_totals_by_reel.get(reel_id, 0) + 1

            review_text = review_row.get("review")
            if review_text is not None and str(review_text).strip():
                engagement_totals_by_reel[reel_id] = engagement_totals_by_reel.get(reel_id, 0) + 1

    for row in rows:
        reel_id = int(row["id"])
        language_codes = _parse_reel_languages(row.get("languages"), language_code_to_name, language_name_to_code)
        if not language_codes:
            continue

        view_count = int(row.get("view_count") or 0)
        is_video = bool(row.get("media_file"))
        reel_total_reviews = review_totals_by_reel.get(reel_id, 0)
        reel_engagement_reviews = engagement_totals_by_reel.get(reel_id, 0)

        for language_code in language_codes:
            if language_code not in stats:
                stats[language_code] = dict(empty_stats)

            language_stats = stats[language_code]
            language_stats["total_reels"] += 1
            language_stats["video_reels" if is_video else "photo_reels"] += 1
            language_stats["total_views"] += view_count
            language_stats["total_reviews"] += reel_total_reviews
            language_stats["engagement_reviews"] += reel_engagement_reviews

    for language_code in language_code_to_name.keys():
        stats.setdefault(language_code, dict(empty_stats))

    sorted_language_codes = sorted(language_code_to_name.keys(), key=lambda code: language_code_to_name[code].lower())
    total_languages_with_reels = sum(1 for code in sorted_language_codes if stats[code]["total_reels"] > 0)
    selected_codes = (
        sorted_language_codes if include_empty else [code for code in sorted_language_codes if stats[code]["total_reels"] > 0]
    )

    items = [
        LanguageReelSummaryItem(
            language_code=code,
            language_name=language_code_to_name[code],
            total_reels=stats[code]["total_reels"],
            photo_reels=stats[code]["photo_reels"],
            video_reels=stats[code]["video_reels"],
            total_views=stats[code]["total_views"],
            total_reviews=stats[code]["total_reviews"],
            engagement_reviews=stats[code]["engagement_reviews"],
        )
        for code in selected_codes
    ]

    return LanguageReelSummaryResponse(
        total_languages=len(sorted_language_codes),
        total_languages_with_reels=total_languages_with_reels,
        items=items,
    )
