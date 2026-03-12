import os
import uuid
import aiofiles
import asyncio
from io import BytesIO
from PIL import Image
from fastapi import UploadFile, HTTPException
from app.config import settings

# ------------------------------
# Constants
# ------------------------------
ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "pdf", "docx", "txt", "mp4", "mp3", "avi", "mkv", "svg",
                      "ai", "eps"]
DEFAULT_MAX_FILE_SIZE_MB = 100  # 10 MB


# ------------------------------
# Helper Functions
# ------------------------------
def _get_extension(filename: str) -> str:
    return filename.split(".")[-1].lower()


def compress_image_sync(content: bytes, size=(800, 800), quality=50) -> bytes:
    try:
        img = Image.open(BytesIO(content))
        img = img.convert("RGB")
        img.thumbnail(size)
        img_io = BytesIO()
        img.save(img_io, format="WEBP", quality=quality)
        return img_io.getvalue()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image compression failed: {e}")


def _get_folder_path(upload_to: str) -> str:
    folder_path = os.path.join(settings.MEDIA_DIR, upload_to)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def _get_file_url(relative_path: str) -> str:
    base = settings.BASE_URL.rstrip("/")
    media_root = settings.MEDIA_ROOT.strip("/")
    return f"{base}/{media_root}/{relative_path}"


def _get_relative_path_from_url(file_url: str) -> str | None:
    try:
        base = f"{settings.BASE_URL.rstrip('/')}/{settings.MEDIA_ROOT.strip('/')}/"
        if not file_url.startswith(base):
            return None
        return file_url.replace(base, "")
    except Exception:
        return None


# ------------------------------
# Core Async File Handlers
# ------------------------------
async def save_file(
        file: UploadFile,
        upload_to: str,
        *,
        max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
        allowed_extensions=ALLOWED_EXTENSIONS,
        compress: bool = True,
        quality: int = 50,
        size=(800, 800),
) -> str:
    ext = _get_extension(file.filename)
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid file type: {ext}")

    folder_path = _get_folder_path(upload_to)

    content = bytearray()
    chunk_size = 1024 * 1024  # 1 MB per read
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_size * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds the allowed limit")

    if compress and ext in {"jpg", "jpeg", "png", "gif"}:
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(
            None, compress_image_sync, bytes(content), size, quality
        )
        filename = f"{uuid.uuid4().hex}.webp"
    else:
        filename = f"{uuid.uuid4().hex}.{ext}"

    file_path = os.path.join(folder_path, filename)
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    relative_path = f"{upload_to}/{filename}"
    return _get_file_url(relative_path)


async def delete_file(file_url: str) -> bool:
    if not file_url:
        return False

    relative_path = _get_relative_path_from_url(file_url)
    if not relative_path:
        return False

    abs_path = os.path.join(settings.MEDIA_DIR, relative_path)
    if os.path.exists(abs_path):
        try:
            os.remove(abs_path)
            return True
        except Exception as e:
            print(f"⚠️ Failed to delete file {abs_path}: {e}")
    return False


async def update_file(
        new_file: UploadFile,
        file_url: str | None,
        upload_to: str,
        *,
        max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
        allowed_extensions=ALLOWED_EXTENSIONS,
        compress: bool = True,
        quality: int = 50,
        size=(800, 800),
) -> str:
    if file_url:
        await delete_file(file_url)

    return await save_file(
        new_file,
        upload_to=upload_to,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        compress=compress,
        quality=quality,
        size=size,
    )
