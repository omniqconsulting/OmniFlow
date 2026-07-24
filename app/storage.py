"""
Cloudflare R2 (S3-compatible) object storage — replaces local-disk uploads
so files survive Render deploys/restarts (Render's default filesystem is
ephemeral and wipes app/static/uploads on every deploy).

Falls back to local disk automatically when R2 isn't configured (no
R2_ACCOUNT_ID env var), so local dev needs no R2 account.

Two buckets:
  - R2_BUCKET_PUBLIC:  catalog/training/product media — served via a
    permanent public URL (R2_PUBLIC_BASE_URL, a custom domain attached
    to the bucket in Cloudflare). Same zero-auth exposure as the old
    /static/uploads/... files had; filenames are random UUIDs so nothing
    is guessable/listable.
  - R2_BUCKET_PRIVATE: employee documents (ID proofs, offer letters,
    gadget docs) — no public access; only reachable via signed_url(),
    which mints a time-limited presigned GET.
"""
import logging
import os

_log = logging.getLogger("app.storage")

UPLOAD_BASE = os.path.join(os.path.dirname(__file__), "static", "uploads")

R2_ACCOUNT_ID = (os.environ.get("R2_ACCOUNT_ID") or "").strip()
R2_ACCESS_KEY_ID = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
R2_SECRET_ACCESS_KEY = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
R2_BUCKET_PUBLIC = (os.environ.get("R2_BUCKET_PUBLIC") or "").strip()
R2_BUCKET_PRIVATE = (os.environ.get("R2_BUCKET_PRIVATE") or "").strip()
R2_PUBLIC_BASE_URL = (os.environ.get("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/")

_R2_VARS = {
    "R2_ACCOUNT_ID": R2_ACCOUNT_ID,
    "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
    "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
    "R2_BUCKET_PUBLIC": R2_BUCKET_PUBLIC,
    "R2_BUCKET_PRIVATE": R2_BUCKET_PRIVATE,
}
R2_ENABLED = all(_R2_VARS.values())

if R2_ENABLED:
    _log.warning("R2 storage ENABLED — uploads will be written to Cloudflare R2 (bucket=%s / %s).", R2_BUCKET_PUBLIC, R2_BUCKET_PRIVATE)
else:
    _missing = [k for k, v in _R2_VARS.items() if not v]
    _log.warning("R2 storage DISABLED — falling back to local disk. Missing/empty env vars: %s", ", ".join(_missing) or "(none — check for a bug)")

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
    return _client


def _local_write(key: str, data: bytes) -> str:
    path = os.path.join(UPLOAD_BASE, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return f"/static/uploads/{key}"


def upload_object(key: str, data: bytes, content_type: str, private: bool = False) -> str:
    """Persists `data` under `key` and returns the value callers should
    store in the DB: a permanent public URL for public objects, or the
    bare R2 key for private ones (signed_url() resolves it at render
    time). Falls back to local disk (old /static/uploads/... behavior)
    when R2 isn't configured."""
    if not R2_ENABLED:
        return _local_write(key, data)

    bucket = R2_BUCKET_PRIVATE if private else R2_BUCKET_PUBLIC
    _get_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    if private:
        return key
    return f"{R2_PUBLIC_BASE_URL}/{key}"


def signed_url(value: str | None, expires_in: int = 3600) -> str | None:
    """Resolves a stored private-object key into a time-limited signed URL.
    Safe to call unconditionally at render time: any value that's already
    a full URL (http/https) or a legacy local /static/... path — or R2
    isn't configured — is returned unchanged."""
    if not value:
        return value
    if value.startswith("http://") or value.startswith("https://") or value.startswith("/static/"):
        return value
    if not R2_ENABLED:
        return value
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET_PRIVATE, "Key": value},
        ExpiresIn=expires_in,
    )


def delete_object(key: str, private: bool = False) -> None:
    """Best-effort delete — no equivalent existed for local-disk uploads
    before this migration, so callers aren't required to use it."""
    if not R2_ENABLED:
        local_path = os.path.join(UPLOAD_BASE, key)
        if os.path.exists(local_path):
            os.remove(local_path)
        return
    bucket = R2_BUCKET_PRIVATE if private else R2_BUCKET_PUBLIC
    _get_client().delete_object(Bucket=bucket, Key=key)
