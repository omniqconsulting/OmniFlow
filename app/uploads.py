"""
Shared file upload infrastructure — Phase 0-E-1, 0-E-3
"""
import os, uuid, io
from fastapi import UploadFile, HTTPException

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

UPLOAD_BASE = os.path.join(os.path.dirname(__file__), "static", "uploads")
MAX_DIM = (1200, 1200)   # auto-compress images to this max size — Phase 0-E-3
ALLOWED_IMAGE = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO = {"video/mp4", "video/quicktime", "video/webm"}
MAX_FILE_MB = 20


def _sniff_video_type(content: bytes) -> str | None:
    """Magic-byte sniffing for the video containers we accept — Pillow can't
    validate these, so we check signatures manually rather than trusting the
    client's declared Content-Type (security audit finding: validation must
    be based on actual file bytes, not filename/MIME header)."""
    if content[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if content[4:8] == b"ftyp":
        # qt/mov brand vs mp4-family brand
        brand = content[8:12]
        if brand in (b"qt  ",):
            return "video/quicktime"
        return "video/mp4"
    return None


def _sniff_image(content: bytes):
    """Returns a validated, re-encodable PIL Image if content is a genuine
    raster image, else None. Uses verify() first (cheap, catches truncated/
    non-image data) then re-opens, since verify() invalidates the handle."""
    if not PIL_AVAILABLE:
        return None
    try:
        probe = Image.open(io.BytesIO(content))
        probe.verify()
    except Exception:
        return None
    try:
        return Image.open(io.BytesIO(content))
    except Exception:
        return None


def _upload_dir(tenant_id: str) -> str:
    path = os.path.join(UPLOAD_BASE, tenant_id)
    os.makedirs(path, exist_ok=True)
    return path


async def save_upload(file: UploadFile, tenant_id: str, allowed_kinds=("image", "video")) -> dict:
    """
    Read, validate (by actual file content, not client-declared type),
    optionally compress, and persist an uploaded file.
    Returns a dict with file_name, file_path (URL), file_type, file_size.
    Raises HTTPException(415) if the real content doesn't match an allowed
    kind, regardless of what Content-Type/filename the client sent.
    """
    content = await file.read()
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {MAX_FILE_MB} MB.")

    orig_name = file.filename or "upload"
    uid = str(uuid.uuid4())

    img = _sniff_image(content) if "image" in allowed_kinds else None
    if img is not None:
        img.thumbnail(MAX_DIM, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        content = buf.getvalue()
        ext = ".jpg"
        ct = "image/jpeg"
    else:
        video_ct = _sniff_video_type(content) if "video" in allowed_kinds else None
        if video_ct is None:
            raise HTTPException(415, "Unsupported or unrecognized file type.")
        ct = video_ct
        ext = os.path.splitext(orig_name)[1].lower() or ".bin"

    filename = f"{uid}{ext}"
    upload_dir = _upload_dir(tenant_id)
    with open(os.path.join(upload_dir, filename), "wb") as fh:
        fh.write(content)

    return {
        "file_name": orig_name,
        "file_path": f"/static/uploads/{tenant_id}/{filename}",
        "file_type": ct,
        "file_size": len(content),
    }
