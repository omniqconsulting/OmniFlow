"""add department_id to knowledge_items + migrate training_materials

Phase 4 (Training/Knowledge consolidation): KnowledgeItem becomes the single
stable table for training/knowledge content. This adds the department_id
column TrainingMaterial had but KnowledgeItem was missing, then copies every
existing TrainingMaterial row into knowledge_items (media_kind inferred from
file_type, department_id/category carried across). training_materials /
training_material_categories are left in place, untouched and unwritten-to
going forward, for rollback safety — not dropped in this migration.

Revision ID: d1e2p3a4r5t6
Revises: g1h2i3j4k5l6
Create Date: 2026-07-03
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2p3a4r5t6'
down_revision: Union[str, Sequence[str], None] = 'g1h2i3j4k5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _media_kind_from_file_type(file_type: str) -> str:
    ft = (file_type or "").lower()
    if ft.startswith("image/") or ft in ("jpg", "jpeg", "png", "gif", "webp"):
        return "image"
    if ft.startswith("video/") or ft in ("mp4", "mov", "avi", "webm", "mkv"):
        return "video"
    if ft.startswith("audio/") or ft in ("mp3", "wav", "ogg", "m4a"):
        return "audio"
    return "document"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "knowledge_items" not in existing_tables:
        return

    ki_cols = {c["name"] for c in inspector.get_columns("knowledge_items")}
    if "department_id" not in ki_cols:
        op.add_column("knowledge_items", sa.Column("department_id", sa.String(), nullable=True))

    if "training_materials" not in existing_tables:
        return

    materials = conn.execute(sa.text(
        "SELECT id, tenant_id, title, description, file_name, file_path, file_type, "
        "file_size, category, department_id, tags, uploaded_by_id, is_deleted, created_at "
        "FROM training_materials"
    )).mappings().all()

    for m in materials:
        already = conn.execute(sa.text(
            "SELECT 1 FROM knowledge_items WHERE tenant_id = :tenant_id AND title = :title "
            "AND file_name = :file_name LIMIT 1"
        ), {
            "tenant_id": m["tenant_id"], "title": m["title"], "file_name": m["file_name"],
        }).first()
        if already:
            continue

        conn.execute(sa.text(
            "INSERT INTO knowledge_items (id, tenant_id, title, description, category, tags, "
            "media_kind, file_url, file_name, file_type, file_size, external_url, department_id, "
            "created_by_id, created_at, updated_at, is_deleted) "
            "VALUES (:id, :tenant_id, :title, :description, :category, :tags, :media_kind, "
            ":file_url, :file_name, :file_type, :file_size, NULL, :department_id, "
            ":created_by_id, :created_at, :updated_at, :is_deleted)"
        ), {
            "id": str(uuid.uuid4()),
            "tenant_id": m["tenant_id"],
            "title": m["title"],
            "description": m["description"],
            "category": m["category"],
            "tags": m["tags"],
            "media_kind": _media_kind_from_file_type(m["file_type"]),
            "file_url": m["file_path"],
            "file_name": m["file_name"],
            "file_type": m["file_type"],
            "file_size": m["file_size"],
            "department_id": m["department_id"],
            "created_by_id": m["uploaded_by_id"],
            "created_at": m["created_at"],
            "updated_at": m["created_at"],
            "is_deleted": m["is_deleted"],
        })


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "knowledge_items" not in inspector.get_table_names():
        return
    ki_cols = {c["name"] for c in inspector.get_columns("knowledge_items")}
    if "department_id" in ki_cols:
        op.drop_column("knowledge_items", "department_id")
