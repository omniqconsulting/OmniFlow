"""catalog_hierarchy

Category -> SubCategory -> Product (parent) -> ProductVariant (the sellable
SKU). Splits the old atomic `products` row into a shared-attribute parent
`products` row plus a `product_variants` row that carries everything that
used to be SKU-level (sku_code, media, tier, low_stock_threshold).

Data migration strategy (zero data loss, no downstream data rewrite needed):
for every existing `products` row we create exactly one new parent `products`
row (fresh id) and exactly one `product_variants` row that REUSES THE OLD
PRODUCT ID as its own id. Every downstream table's `product_id` column is
then simply renamed to `variant_id` in place — the values already point at
the right row, since `product_variants.id == old products.id`. No UPDATE
statements are needed on the 9 downstream tables. The `products` table itself
is never renamed/dropped (only ALTER COLUMN in place), so no other table's
foreign-key reflection breaks mid-migration.

Revision ID: g1h2i3j4k5l6
Revises: a3b4c5d6e7f8
Create Date: 2026-07-03
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# product_id -> variant_id rename, applied identically to each of these tables
_RENAME_TABLES = [
    "product_stock",
    "stock_ledger",
    "inventory_po_items",
    "price_list_items",
    "price_list_item_history",
    "customer_price_overrides",
    "cost_entries",
    "sales_order_items",
    "stock_reservations",
]


def _drop_stale_product_fk(conn, inspector, table):
    """Postgres only: existing FK constraints reference products(id); once the
    column is renamed to variant_id it must no longer be constrained against
    products — drop it (SQLite never enforces FKs here, so left as-is there)."""
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == "products" and "product_id" in (fk.get("constrained_columns") or []):
            name = fk.get("name")
            if name:
                try:
                    op.drop_constraint(name, table, type_="foreignkey")
                except Exception:
                    pass


def _rename_product_id_to_variant_id(conn, dialect, inspector, table):
    cols = {c["name"] for c in inspector.get_columns(table)}
    if "product_id" not in cols or "variant_id" in cols:
        return
    if dialect == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("product_id", new_column_name="variant_id")
    else:
        _drop_stale_product_fk(conn, inspector, table)
        conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN product_id TO variant_id"))


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. New hierarchy tables ------------------------------------------------
    if "categories" not in existing_tables:
        op.create_table(
            "categories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "sub_categories" not in existing_tables:
        op.create_table(
            "sub_categories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("category_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "product_variants" not in existing_tables:
        op.create_table(
            "product_variants",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("product_id", sa.String(), nullable=False),
            sa.Column("sku_code", sa.String(), nullable=False),
            sa.Column("variant_label", sa.String(), nullable=True),
            sa.Column("variant_attributes_json", sa.Text(), nullable=True),
            sa.Column("base_unit_id", sa.String(), nullable=True),
            sa.Column("media_urls_json", sa.Text(), nullable=True),
            sa.Column("product_tier", sa.String(), nullable=True),
            sa.Column("low_stock_threshold", sa.Float(), nullable=True),
            sa.Column("end_product_id", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("created_by_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    has_products = "products" in existing_tables
    old_cols = {c["name"] for c in inspector.get_columns("products")} if has_products else set()
    already_migrated = "sub_category_id" in old_cols and "sku_code" not in old_cols

    if has_products and not already_migrated and "sku_code" in old_cols:
        # 2. Capture every existing (atomic) product row before touching schema.
        rows = conn.execute(sa.text(
            "SELECT id, tenant_id, sku_code, name, description, category, base_unit_id, "
            "attributes_json, media_urls_json, product_tier, low_stock_threshold, "
            "is_active, is_deleted, created_by_id, created_at, updated_at FROM products"
        )).mappings().all()

        # 3. Category / SubCategory backfill — one Category per distinct
        #    (tenant_id, category) string, one default "General" SubCategory each.
        category_map = {}   # (tenant_id, category_name) -> category_id
        subcat_map = {}     # (tenant_id, category_name) -> sub_category_id ("General")
        for row in rows:
            key = (row["tenant_id"], (row["category"] or "").strip() or "Uncategorized")
            if key not in category_map:
                cat_id = str(uuid.uuid4())
                category_map[key] = cat_id
                conn.execute(sa.text(
                    "INSERT INTO categories (id, tenant_id, name, is_active, is_deleted) "
                    "VALUES (:id, :tenant_id, :name, 1, 0)"
                ), {"id": cat_id, "tenant_id": key[0], "name": key[1]})
            if key not in subcat_map:
                sub_id = str(uuid.uuid4())
                subcat_map[key] = sub_id
                conn.execute(sa.text(
                    "INSERT INTO sub_categories (id, tenant_id, category_id, name, is_active, is_deleted) "
                    "VALUES (:id, :tenant_id, :category_id, 'General', 1, 0)"
                ), {"id": sub_id, "tenant_id": key[0], "category_id": category_map[key]})

        # 4. product_variants: reuse the OLD product id as the variant id —
        #    every downstream FK value already equals this id, so no rewrite
        #    of downstream data is needed, only the column rename in step 7.
        parent_id_by_old_id = {}
        for row in rows:
            parent_id = str(uuid.uuid4())
            parent_id_by_old_id[row["id"]] = parent_id
            conn.execute(sa.text(
                "INSERT INTO product_variants (id, tenant_id, product_id, sku_code, variant_label, "
                "variant_attributes_json, base_unit_id, media_urls_json, product_tier, "
                "low_stock_threshold, is_active, is_deleted, created_by_id, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :product_id, :sku_code, :variant_label, '{}', :base_unit_id, "
                ":media_urls_json, :product_tier, :low_stock_threshold, :is_active, :is_deleted, "
                ":created_by_id, :created_at, :updated_at)"
            ), {
                "id": row["id"], "tenant_id": row["tenant_id"], "product_id": parent_id,
                "sku_code": row["sku_code"], "variant_label": row["name"],
                "base_unit_id": row["base_unit_id"], "media_urls_json": row["media_urls_json"] or "[]",
                "product_tier": row["product_tier"] or "UNRANKED",
                "low_stock_threshold": row["low_stock_threshold"],
                "is_active": row["is_active"], "is_deleted": row["is_deleted"],
                "created_by_id": row["created_by_id"], "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        # 5. Add sub_category_id to products (nullable), delete the old atomic
        #    rows, then insert the new parent rows — still against the OLD
        #    products schema (sku_code etc. still present but now unused, will
        #    be dropped in step 6).
        if "sub_category_id" not in old_cols:
            op.add_column("products", sa.Column("sub_category_id", sa.String(), nullable=True))

        conn.execute(sa.text("DELETE FROM products"))
        for row in rows:
            key = (row["tenant_id"], (row["category"] or "").strip() or "Uncategorized")
            conn.execute(sa.text(
                "INSERT INTO products (id, tenant_id, sku_code, name, description, category, "
                "sub_category_id, base_unit_id, attributes_json, media_urls_json, product_tier, "
                "low_stock_threshold, is_active, is_deleted, created_by_id, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :sku_code, :name, :description, :category, :sub_category_id, "
                ":base_unit_id, '{}', '[]', NULL, NULL, :is_active, :is_deleted, :created_by_id, "
                ":created_at, :updated_at)"
            ), {
                "id": parent_id_by_old_id[row["id"]], "tenant_id": row["tenant_id"],
                "sku_code": f"__parent_{row['id'][:8]}", "name": row["name"],
                "description": row["description"], "category": row["category"],
                "sub_category_id": subcat_map[key], "base_unit_id": row["base_unit_id"],
                "is_active": row["is_active"], "is_deleted": row["is_deleted"],
                "created_by_id": row["created_by_id"], "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        parent_total = conn.execute(sa.text("SELECT COUNT(*) FROM products")).scalar()
        variant_total = conn.execute(sa.text("SELECT COUNT(*) FROM product_variants")).scalar()
        assert parent_total == len(rows) and variant_total >= len(rows), (
            "Catalog hierarchy backfill row-count mismatch — aborting migration"
        )

        # 6. Drop the now-obsolete SKU-level columns from products.
        if dialect == "sqlite":
            with op.batch_alter_table("products") as batch_op:
                batch_op.drop_column("sku_code")
                batch_op.drop_column("media_urls_json")
                batch_op.drop_column("product_tier")
                batch_op.drop_column("low_stock_threshold")
                batch_op.drop_column("category")
        else:
            for col in ("sku_code", "media_urls_json", "product_tier", "low_stock_threshold", "category"):
                conn.execute(sa.text(f"ALTER TABLE products DROP COLUMN IF EXISTS {col}"))
    elif has_products and "sub_category_id" not in old_cols:
        # Fresh/empty products table (no rows) — just bring the schema up to date.
        op.add_column("products", sa.Column("sub_category_id", sa.String(), nullable=True))
        if "sku_code" in old_cols:
            if dialect == "sqlite":
                with op.batch_alter_table("products") as batch_op:
                    for col in ("sku_code", "media_urls_json", "product_tier", "low_stock_threshold", "category"):
                        if col in old_cols:
                            batch_op.drop_column(col)
            else:
                for col in ("sku_code", "media_urls_json", "product_tier", "low_stock_threshold", "category"):
                    conn.execute(sa.text(f"ALTER TABLE products DROP COLUMN IF EXISTS {col}"))

    # 7. Rename product_id -> variant_id on every downstream table (data untouched).
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    for table in _RENAME_TABLES:
        if table not in existing_tables:
            continue
        _rename_product_id_to_variant_id(conn, dialect, inspector, table)
        inspector = sa.inspect(conn)

    # tier_snapshots.entity_id already stores the old product id for
    # entity_type='PRODUCT' rows — those values are unchanged (variant ids
    # equal old product ids), so no data update is required there.

    # 8. Link pre-existing Setup > End Products rows to their matching new
    # ProductVariant by (tenant_id, sku_code), per user request that Catalog
    # and End Products stay in sync going forward.
    existing_tables = sa.inspect(conn).get_table_names()
    if "end_products" in existing_tables:
        end_products = conn.execute(sa.text(
            "SELECT id, tenant_id, sku_code FROM end_products WHERE sku_code IS NOT NULL"
        )).mappings().all()
        for ep in end_products:
            conn.execute(sa.text(
                "UPDATE product_variants SET end_product_id = :ep_id "
                "WHERE tenant_id = :tenant_id AND sku_code = :sku_code AND end_product_id IS NULL"
            ), {"ep_id": ep["id"], "tenant_id": ep["tenant_id"], "sku_code": ep["sku_code"]})


def downgrade() -> None:
    """Best-effort only — the Category/SubCategory/Variant split is lossy to
    reverse cleanly (multiple variants could exist per product by the time a
    downgrade runs), so this restores shape, not guaranteed 1:1 data."""
    conn = op.get_bind()
    dialect = conn.dialect.name
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    for table in _RENAME_TABLES:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "variant_id" in cols and "product_id" not in cols:
            if dialect == "sqlite":
                with op.batch_alter_table(table) as batch_op:
                    batch_op.alter_column("variant_id", new_column_name="product_id")
            else:
                conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN variant_id TO product_id"))

    if "product_variants" in existing_tables:
        op.drop_table("product_variants")
    if "sub_categories" in existing_tables:
        op.drop_table("sub_categories")
    if "categories" in existing_tables:
        op.drop_table("categories")
