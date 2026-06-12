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


def _upload_dir(tenant_id: str) -> str:
    path = os.path.join(UPLOAD_BASE, tenant_id)
    os.makedirs(path, exist_ok=True)
    return path


async def save_upload(file: UploadFile, tenant_id: str) -> dict:
    """
    Read, optionally compress, and persist an uploaded file.
    Returns a dict with file_name, file_path (URL), file_type, file_size.
    """
    content = await file.read()
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {MAX_FILE_MB} MB.")

    ct = (file.content_type or "").lower()
    orig_name = file.filename or "upload"
    ext = os.path.splitext(orig_name)[1].lower() or ".bin"
    uid = str(uuid.uuid4())

    # Auto-compress images (Phase 0-E-3)
    if ct in ALLOWED_IMAGE and PIL_AVAILABLE:
        try:
            img = Image.open(io.BytesIO(content))
            img.thumbnail(MAX_DIM, Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            content = buf.getvalue()
            ext = ".jpg"
            ct = "image/jpeg"
        except Exception:
            pass  # fall back to raw

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
