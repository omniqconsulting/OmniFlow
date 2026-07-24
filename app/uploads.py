"""
Shared file upload infrastructure — Phase 0-E-1, 0-E-3

File bytes are persisted via app/storage.py (Cloudflare R2, falling back to
local disk when R2 isn't configured) rather than written to disk directly —
Render's filesystem is ephemeral and previously wiped uploads on every
deploy/restart.
"""
import os, uuid, io
from fastapi import UploadFile, HTTPException

from . import storage

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

MAX_DIM = (1200, 1200)   # auto-compress images to this max size — Phase 0-E-3
ALLOWED_IMAGE = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_PDF = {"application/pdf"}
ALLOWED_AUDIO = {"audio/mpeg", "audio/wav", "audio/mp4", "audio/ogg"}
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


def _sniff_pdf_type(content: bytes) -> str | None:
    return "application/pdf" if content[:5] == b"%PDF-" else None


def _sniff_audio_type(content: bytes) -> str | None:
    """Magic-byte sniffing for common audio containers."""
    if content[:3] == b"ID3" or content[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "audio/wav"
    if content[4:8] == b"ftyp" and content[8:12] in (b"M4A ", b"mp42", b"isom"):
        return "audio/mp4"
    if content[:4] == b"OggS":
        return "audio/ogg"
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


async def save_upload(file: UploadFile, tenant_id: str, allowed_kinds=("image", "video"), private: bool = False) -> dict:
    """
    Read, validate (by actual file content, not client-declared type),
    optionally compress, and persist an uploaded file to object storage.
    Returns a dict with file_name, file_path (URL, or an object key for
    private uploads — see app/storage.py::signed_url), file_type, file_size.
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
        ct = None
        if "video" in allowed_kinds:
            ct = _sniff_video_type(content)
        if ct is None and "pdf" in allowed_kinds:
            ct = _sniff_pdf_type(content)
        if ct is None and "audio" in allowed_kinds:
            ct = _sniff_audio_type(content)
        if ct is None:
            raise HTTPException(415, "Unsupported or unrecognized file type.")
        ext = os.path.splitext(orig_name)[1].lower() or ".bin"

    filename = f"{uid}{ext}"
    key = f"{tenant_id}/{filename}"
    file_path = storage.upload_object(key, content, ct, private=private)

    return {
        "file_name": orig_name,
        "file_path": file_path,
        "file_type": ct,
        "file_size": len(content),
    }
