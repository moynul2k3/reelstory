from io import BytesIO
from typing import Optional, Set, Tuple

from fastapi import BackgroundTasks, HTTPException, UploadFile

from app.utils.file_manager import save_file, update_file
from applications.reels.reels import Reel

VIDEO_EXTENSIONS: Set[str] = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp", "svg", "gif"}
MAX_VIDEO_SIZE_MB = 300
MAX_IMAGE_SIZE_MB = 20
UploadPayload = Tuple[str, bytes]


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


async def _prepare_upload_payload(
    file: Optional[UploadFile],
    *,
    label: str,
    max_size_mb: int,
    allowed_extensions: Set[str],
) -> Optional[UploadPayload]:
    if not file or not file.filename:
        return None

    filename = file.filename.strip()
    if "." not in filename:
        raise HTTPException(status_code=400, detail=f"{label} has no valid extension")

    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid {label} type: {extension}")

    content = await _read_upload_bytes(file, max_size_mb=max_size_mb, label=label)
    return filename, content


async def _save_or_update_uploaded_file(
    file: UploadFile,
    *,
    existing_url: Optional[str],
    upload_to: str,
    max_size: int,
    allowed_extensions: Set[str],
    compress: bool,
) -> str:
    if existing_url:
        return await update_file(
            file,
            existing_url,
            upload_to=upload_to,
            max_size=max_size,
            allowed_extensions=allowed_extensions,
            compress=compress,
        )
    return await save_file(
        file,
        upload_to=upload_to,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        compress=compress,
    )


async def process_reel_uploads_in_background(
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
            media_upload = UploadFile(file=BytesIO(media_content), filename=media_name)
            update_data["media_file"] = await _save_or_update_uploaded_file(
                media_upload,
                existing_url=old_media_url,
                upload_to="reels/videos",
                max_size=MAX_VIDEO_SIZE_MB,
                allowed_extensions=VIDEO_EXTENSIONS,
                compress=False,
            )

        if thumbnail_payload:
            thumb_name, thumb_content = thumbnail_payload
            thumb_upload = UploadFile(file=BytesIO(thumb_content), filename=thumb_name)
            update_data["thumbnail"] = await _save_or_update_uploaded_file(
                thumb_upload,
                existing_url=old_thumbnail_url,
                upload_to="reels/thumbnails",
                max_size=MAX_IMAGE_SIZE_MB,
                allowed_extensions=IMAGE_EXTENSIONS,
                compress=True,
            )

        if logo_payload:
            logo_name, logo_content = logo_payload
            logo_upload = UploadFile(file=BytesIO(logo_content), filename=logo_name)
            update_data["logo"] = await _save_or_update_uploaded_file(
                logo_upload,
                existing_url=old_logo_url,
                upload_to="reels/logos",
                max_size=MAX_IMAGE_SIZE_MB,
                allowed_extensions=IMAGE_EXTENSIONS,
                compress=True,
            )

        if not update_data:
            return

        reel = await Reel.get_or_none(id=reel_id)
        if not reel:
            return

        for field, value in update_data.items():
            setattr(reel, field, value)
        await reel.save(update_fields=list(update_data.keys()))
    except Exception as error:
        print(f"[reel-upload] failed for reel_id={reel_id}: {error}")


async def prepare_reel_upload_payloads(
    *,
    media_file: Optional[UploadFile] = None,
    thumbnail: Optional[UploadFile] = None,
    logo: Optional[UploadFile] = None,
) -> Tuple[Optional[UploadPayload], Optional[UploadPayload], Optional[UploadPayload]]:
    media_payload = await _prepare_upload_payload(
        media_file,
        label="media_file",
        max_size_mb=MAX_VIDEO_SIZE_MB,
        allowed_extensions=VIDEO_EXTENSIONS,
    )
    thumbnail_payload = await _prepare_upload_payload(
        thumbnail,
        label="thumbnail",
        max_size_mb=MAX_IMAGE_SIZE_MB,
        allowed_extensions=IMAGE_EXTENSIONS,
    )
    logo_payload = await _prepare_upload_payload(
        logo,
        label="logo",
        max_size_mb=MAX_IMAGE_SIZE_MB,
        allowed_extensions=IMAGE_EXTENSIONS,
    )
    return media_payload, thumbnail_payload, logo_payload


def queue_reel_upload_task(
    background_tasks: BackgroundTasks,
    *,
    reel_id: int,
    media_payload: Optional[UploadPayload] = None,
    thumbnail_payload: Optional[UploadPayload] = None,
    logo_payload: Optional[UploadPayload] = None,
    old_media_url: Optional[str] = None,
    old_thumbnail_url: Optional[str] = None,
    old_logo_url: Optional[str] = None,
) -> str:
    if not any((media_payload, thumbnail_payload, logo_payload)):
        return "not_provided"

    background_tasks.add_task(
        process_reel_uploads_in_background,
        reel_id=reel_id,
        media_payload=media_payload,
        thumbnail_payload=thumbnail_payload,
        logo_payload=logo_payload,
        old_media_url=old_media_url,
        old_thumbnail_url=old_thumbnail_url,
        old_logo_url=old_logo_url,
    )
    return "processing"
