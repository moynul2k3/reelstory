import json
import random

from tortoise import Tortoise
from tortoise.exceptions import OperationalError

from app.config import settings
from applications.reels.category import Category
from applications.reels.reels import Reel, ReelsReview
from applications.user.models import User

CATEGORY_COUNT = 10
REELS_PER_CATEGORY = 10
MAX_REVIEWS_PER_REEL = 5

PEXELS_VIDEO_URLS = [
    "https://videos.pexels.com/video-files/855404/855404-hd_1920_1080_25fps.mp4",
    "https://videos.pexels.com/video-files/3129957/3129957-uhd_2560_1440_25fps.mp4",
    "https://videos.pexels.com/video-files/3195394/3195394-uhd_2560_1440_25fps.mp4",
    "https://videos.pexels.com/video-files/4623188/4623188-hd_1920_1080_30fps.mp4",
    "https://videos.pexels.com/video-files/5453622/5453622-hd_1920_1080_25fps.mp4",
]

PEXELS_THUMBNAIL_URLS = [
    "https://images.pexels.com/photos/248797/pexels-photo-248797.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/210186/pexels-photo-210186.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/325185/pexels-photo-325185.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/531880/pexels-photo-531880.jpeg?auto=compress&cs=tinysrgb&w=1200",
]


def _pick_media_urls(category_index: int, reel_index: int) -> tuple[str, str]:
    index = (category_index * 31 + reel_index * 17) % len(PEXELS_VIDEO_URLS)
    return PEXELS_VIDEO_URLS[index], PEXELS_THUMBNAIL_URLS[index]


async def _ensure_mysql_reels_schema() -> None:
    engine = (settings.DB_ENGINE or "").lower()
    if engine not in {"mysql", "mariadb"}:
        return

    conn = Tortoise.get_connection("default")
    schema_fixes = [
        ("bonuses", "ALTER TABLE `reels` MODIFY COLUMN `bonuses` INT NOT NULL DEFAULT 0;"),
        ("languages", "ALTER TABLE `reels` MODIFY COLUMN `languages` VARCHAR(50) NOT NULL DEFAULT 'en';"),
        ("share_count", "ALTER TABLE `reels` MODIFY COLUMN `share_count` INT NOT NULL DEFAULT 0;"),
    ]

    for field_name, statement in schema_fixes:
        try:
            await conn.execute_script(statement)
        except Exception as error:
            print(f"[dummy-reels] schema fix skipped for {field_name}: {error}")

    try:
        await conn.execute_script(
            "UPDATE `reels` SET `languages` = TRIM(BOTH '\"' FROM `languages`) "
            "WHERE `languages` LIKE '\"%\"';"
        )
    except Exception as error:
        print(f"[dummy-reels] languages cleanup skipped: {error}")


async def create_test_reels_data() -> None:
    created_categories = 0
    created_reels = 0
    created_reviews = 0

    await _ensure_mysql_reels_schema()

    users = await User.filter(is_active=True).all()
    if not users:
        print("[dummy-reels] no active users found; reviews will not be created.")

    for category_index in range(1, CATEGORY_COUNT + 1):
        category_name = f"Category {category_index:02d}"
        category, category_created = await Category.get_or_create(name=category_name)
        if category_created:
            created_categories += 1

        for reel_index in range(1, REELS_PER_CATEGORY + 1):
            title = f"Reel {category_index:02d}-{reel_index:02d}"
            media_file_url, thumbnail_url = _pick_media_urls(category_index, reel_index)
            reel_defaults = {
                "category_id": category.id,
                "bonuses": random.randint(5, 500),
                "short_description": f"Short description for {title}",
                "terms_highlights": f"Terms highlights for {title}",
                "affiliate_link": f"https://example.com/reels/{category_index:02d}-{reel_index:02d}",
                "languages": "en",
                "tags": [f"cat-{category_index:02d}", f"reel-{reel_index:02d}"],
                "disclaimers": "Dummy data for development environment.",
                "media_file": media_file_url,
                "thumbnail": thumbnail_url,
                "is_adult_content": False,
                "is_active": True,
            }

            try:
                reel, reel_created = await Reel.get_or_create(
                    title=title,
                    defaults=reel_defaults,
                )
            except OperationalError as error:
                error_text = str(error)
                if "reels.languages" in error_text or "reels.bonuses" in error_text:
                    fallback_defaults = dict(reel_defaults)
                    fallback_defaults["languages"] = json.dumps(reel_defaults["languages"])
                    fallback_defaults["bonuses"] = json.dumps(reel_defaults["bonuses"])
                    try:
                        reel, reel_created = await Reel.get_or_create(
                            title=title,
                            defaults=fallback_defaults,
                        )
                    except OperationalError as retry_error:
                        print(f"[dummy-reels] failed creating {title} after fallback: {retry_error}")
                        continue
                else:
                    print(f"[dummy-reels] failed creating {title}: {error}")
                    continue

            if reel_created:
                created_reels += 1
            else:
                update_fields = []
                if reel.category_id != category.id:
                    reel.category_id = category.id
                    update_fields.append("category_id")
                if not reel.media_file:
                    reel.media_file = media_file_url
                    update_fields.append("media_file")
                if not reel.thumbnail:
                    reel.thumbnail = thumbnail_url
                    update_fields.append("thumbnail")

                if update_fields:
                    await reel.save(update_fields=update_fields)

            if not users:
                continue

            existing_reviews = await ReelsReview.filter(
                reel_id=reel.id,
                parent_id__isnull=True,
            ).order_by("created_at")

            existing_reviews_count = len(existing_reviews)
            if existing_reviews_count > MAX_REVIEWS_PER_REEL:
                extra_review_ids = [review.id for review in existing_reviews[MAX_REVIEWS_PER_REEL:]]
                await ReelsReview.filter(id__in=extra_review_ids).delete()
                existing_reviews_count = MAX_REVIEWS_PER_REEL

            missing_reviews = max(0, MAX_REVIEWS_PER_REEL - existing_reviews_count)
            for review_index in range(missing_reviews):
                reviewer = users[(category_index + reel_index + review_index) % len(users)]
                rating = (review_index % 5) + 1
                review_text = f"Dummy review {review_index + 1} for {title}"

                await ReelsReview.create(
                    reel_id=reel.id,
                    user_id=reviewer.id,
                    rating=rating,
                    review=review_text,
                    parent_id=None,
                )
                created_reviews += 1

    print(
        "[dummy-reels] seeding completed "
        f"(categories_created={created_categories}, reels_created={created_reels}, reviews_created={created_reviews})"
    )
