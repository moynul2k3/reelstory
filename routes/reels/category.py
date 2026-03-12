import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from pydantic import BaseModel
from tortoise.contrib.pydantic import pydantic_model_creator

from app.auth import permission_required
from applications.reels.category import Category
from applications.reels.reels import Reel

router = APIRouter(prefix="/categories", tags=["Reel Categories"])

CategoryOut = pydantic_model_creator(Category, name="ReelCategoryOut")


class RandomReelOut(BaseModel):
    id: int
    title: str
    bonuses: int
    media_file: Optional[str] = None
    thumbnail: Optional[str] = None
    logo: Optional[str] = None
    is_active: bool


class CategoryWithRandomReelOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    random_reel: Optional[RandomReelOut] = None


def _normalize_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Category name is required")
    return cleaned


@router.post(
    "/",
    response_model=CategoryOut,
    dependencies=[Depends(permission_required("add_category"))],
)
async def create_category(name: str = Form(...)):
    cleaned_name = _normalize_name(name)

    if await Category.filter(name__iexact=cleaned_name).exists():
        raise HTTPException(status_code=400, detail="Category already exists")

    category = await Category.create(name=cleaned_name)
    return await CategoryOut.from_tortoise_orm(category)


@router.get("/", response_model=List[CategoryWithRandomReelOut])
async def list_categories(
    search: Optional[str] = Query(None, description="Filter by category name"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    queryset = Category.all()

    if search:
        queryset = queryset.filter(name__icontains=search.strip())

    categories = await queryset.offset(offset).limit(limit)
    if not categories:
        return []

    category_ids = [category.id for category in categories]
    reels = await Reel.filter(category_id__in=category_ids).values(
        "id",
        "title",
        "bonuses",
        "media_file",
        "thumbnail",
        "logo",
        "is_active",
        "category_id",
    )

    reels_by_category: Dict[int, List[Dict[str, Any]]] = {}
    for reel in reels:
        category_reels = reels_by_category.setdefault(reel["category_id"], [])
        category_reels.append(reel)

    response: List[CategoryWithRandomReelOut] = []
    for category in categories:
        random_reel = None
        if reels_by_category.get(category.id):
            selected = random.choice(reels_by_category[category.id])
            random_reel = RandomReelOut(
                id=selected["id"],
                title=selected["title"],
                bonuses=selected["bonuses"],
                media_file=selected["media_file"],
                thumbnail=selected["thumbnail"],
                logo=selected["logo"],
                is_active=selected["is_active"],
            )

        response.append(
            CategoryWithRandomReelOut(
                id=category.id,
                name=category.name,
                created_at=category.created_at,
                random_reel=random_reel,
            )
        )

    return response


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(category_id: int):
    category = await Category.get_or_none(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    return await CategoryOut.from_tortoise_orm(category)


@router.put(
    "/{category_id}",
    response_model=CategoryOut,
    dependencies=[Depends(permission_required("update_category"))],
)
async def update_category(
    category_id: int,
    name: str = Form(...),
):
    category = await Category.get_or_none(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    cleaned_name = _normalize_name(name)
    if cleaned_name.lower() != category.name.lower():
        exists = await Category.filter(name__iexact=cleaned_name).exclude(id=category_id).exists()
        if exists:
            raise HTTPException(status_code=400, detail="Category name already exists")
        category.name = cleaned_name
        await category.save(update_fields=["name"])

    return await CategoryOut.from_tortoise_orm(category)


@router.delete(
    "/{category_id}",
    response_model=dict,
    dependencies=[Depends(permission_required("delete_category"))],
)
async def delete_category(category_id: int):
    category = await Category.get_or_none(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    has_reels = await Reel.filter(category_id=category_id).exists()
    if has_reels:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete category with associated reels",
        )

    await category.delete()
    return {"detail": "Category deleted successfully"}
