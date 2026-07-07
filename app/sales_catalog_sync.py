"""
Keeps Setup > End Products (app/setup_routes.py, used by FMS/Delegations
linked entities) in sync with the Sales Catalog's ProductVariant rows.
Both sides may create Category/SubCategory/Product/ProductVariant hierarchy
data — matched on (tenant_id, sku_code) — so the two lists stay bidirectionally
in sync in real time.

Split into its own module (rather than living in sales_catalog.py or
setup_routes.py) to avoid an import cycle between those two files.
"""
import json
import re
import uuid as _uuid
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from .database import (
    new_id, Category, SubCategory, Product, ProductVariant, ProductStock,
    UnitOfMeasure, EndProduct,
)

_DRIVE_LINK_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")


def resolve_or_create_category_pair(
    db: Session, tenant_id: str, category_name: str, sub_category_name: str,
) -> SubCategory:
    """Find-or-create Category -> SubCategory for this tenant. Shared by
    Sales Catalog bulk upload and Setup > End Products so both sides can
    independently introduce new hierarchy entries."""
    cat_name = (category_name or "").strip() or "Uncategorized"
    sub_name = (sub_category_name or "").strip() or "General"

    category = db.query(Category).filter(
        Category.tenant_id == tenant_id, Category.name == cat_name, Category.is_deleted == False,
    ).first()
    if not category:
        category = Category(id=new_id(), tenant_id=tenant_id, name=cat_name)
        db.add(category)
        db.flush()

    sub_category = db.query(SubCategory).filter(
        SubCategory.tenant_id == tenant_id, SubCategory.category_id == category.id,
        SubCategory.name == sub_name, SubCategory.is_deleted == False,
    ).first()
    if not sub_category:
        sub_category = SubCategory(id=new_id(), tenant_id=tenant_id, category_id=category.id, name=sub_name)
        db.add(sub_category)
        db.flush()

    return sub_category


def resolve_or_create_hierarchy(
    db: Session, tenant_id: str, category_name: str, sub_category_name: str,
    product_name: str, product_description: str = None, user_id: str = None,
) -> Product:
    """Find-or-create Category -> SubCategory -> Product for this tenant."""
    prod_name = (product_name or "").strip()
    sub_category = resolve_or_create_category_pair(db, tenant_id, category_name, sub_category_name)

    product = db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.sub_category_id == sub_category.id,
        Product.name == prod_name, Product.is_deleted == False,
    ).first()
    if not product:
        product = Product(
            id=new_id(), tenant_id=tenant_id, name=prod_name,
            description=(product_description or "").strip() or None,
            sub_category_id=sub_category.id, created_by_id=user_id,
        )
        db.add(product)
        db.flush()

    return product


async def attach_drive_photo(variant: ProductVariant, drive_link: str) -> Optional[str]:
    """Extract FILE_ID from a Drive share link, download it, validate it's an
    image, save alongside other variant photos. Returns an error string on
    failure, or None on success."""
    m = _DRIVE_LINK_RE.search(drive_link) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", drive_link)
    if not m:
        return f"Could not parse a Drive file id from '{drive_link}'"
    file_id = m.group(1)
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url)
    except Exception as e:
        return f"Could not fetch Drive link: {e}"
    if resp.status_code != 200:
        return f"Drive file not accessible (HTTP {resp.status_code}) — check it's shared 'Anyone with the link'"
    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return f"Drive link did not return an image (got '{content_type}')"
    content = resp.content
    if len(content) > 5 * 1024 * 1024:
        return "Drive image exceeds 5MB limit"
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(content_type.split(";")[0], "jpg")
    filename = f"{_uuid.uuid4().hex}.{ext}"
    rel_path = f"uploads/{variant.tenant_id}/products/{variant.id}/{filename}"
    full_path = Path(__file__).parent / "static" / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)

    existing = json.loads(variant.media_urls_json or "[]")
    if len(existing) < 8:
        existing.append(rel_path)
        variant.media_urls_json = json.dumps(existing)
    return None


def sync_end_product_from_variant(db: Session, variant: ProductVariant) -> None:
    """Upsert the EndProduct mirror for this variant. Call after commit-worthy
    variant create/update. Does not commit — caller controls the transaction."""
    if not variant.sku_code:
        return
    name = variant.variant_label or (variant.product.name if variant.product else variant.sku_code)

    end_product = None
    if variant.end_product_id:
        end_product = db.query(EndProduct).filter(EndProduct.id == variant.end_product_id).first()
    if not end_product:
        end_product = db.query(EndProduct).filter(
            EndProduct.tenant_id == variant.tenant_id,
            EndProduct.sku_code == variant.sku_code,
            EndProduct.is_deleted == False,
        ).first()

    unit_abbr = variant.base_unit.abbreviation if variant.base_unit else (
        variant.product.base_unit.abbreviation if variant.product and variant.product.base_unit else None
    )
    sub_category_id = variant.product.sub_category_id if variant.product else None
    category_id = variant.product.sub_category.category_id if variant.product and variant.product.sub_category else None

    if end_product:
        end_product.name = name
        end_product.sku_code = variant.sku_code
        end_product.unit = unit_abbr
        end_product.is_active = variant.is_active
        end_product.category_id = category_id
        end_product.sub_category_id = sub_category_id
    else:
        end_product = EndProduct(
            tenant_id=variant.tenant_id, name=name, sku_code=variant.sku_code,
            unit=unit_abbr, created_by_id=variant.created_by_id,
            category_id=category_id, sub_category_id=sub_category_id,
        )
        db.add(end_product)
        db.flush()

    variant.end_product_id = end_product.id


def remove_end_product_for_variant(db: Session, variant: ProductVariant) -> None:
    """Soft-delete the mirrored EndProduct when its variant is deleted."""
    if not variant.end_product_id:
        return
    end_product = db.query(EndProduct).filter(EndProduct.id == variant.end_product_id).first()
    if end_product:
        end_product.is_deleted = True


def sync_variant_from_end_product(
    db: Session, end_product: EndProduct, *,
    variant_label: str = None, low_stock_threshold: float = None,
) -> Optional[ProductVariant]:
    """Reverse direction: keep an existing/new ProductVariant in sync with an
    End Product edit. If no variant exists for this sku_code yet, and a
    category/sub_category/name is present, create the full Category ->
    SubCategory -> Product -> ProductVariant chain — End Products can
    introduce new hierarchy entries just like Catalog's bulk upload can.
    Does not commit — caller controls the transaction."""
    if not end_product.sku_code:
        return None

    variant = db.query(ProductVariant).filter(
        ProductVariant.tenant_id == end_product.tenant_id,
        ProductVariant.sku_code == end_product.sku_code,
        ProductVariant.is_deleted == False,
    ).first()

    unit = None
    if end_product.unit:
        unit = db.query(UnitOfMeasure).filter(
            UnitOfMeasure.tenant_id == end_product.tenant_id,
            UnitOfMeasure.abbreviation == end_product.unit,
            UnitOfMeasure.is_active == True,
        ).first()

    if variant:
        if unit:
            variant.base_unit_id = unit.id
        if low_stock_threshold is not None:
            variant.low_stock_threshold = low_stock_threshold
        variant.is_active = end_product.is_active
        variant.end_product_id = end_product.id
        return variant

    category_name = end_product.category.name if end_product.category else None
    sub_category_name = end_product.sub_category.name if end_product.sub_category else None
    product = resolve_or_create_hierarchy(
        db, end_product.tenant_id, category_name, sub_category_name,
        end_product.name, end_product.description, end_product.created_by_id,
    )
    variant = ProductVariant(
        id=new_id(), tenant_id=end_product.tenant_id, product_id=product.id,
        sku_code=end_product.sku_code,
        variant_label=(variant_label or "").strip() or end_product.name,
        base_unit_id=unit.id if unit else None,
        low_stock_threshold=low_stock_threshold,
        end_product_id=end_product.id,
        created_by_id=end_product.created_by_id,
    )
    db.add(variant)
    db.flush()
    db.add(ProductStock(variant_id=variant.id, tenant_id=end_product.tenant_id))
    end_product.category_id = product.sub_category.category_id if product.sub_category else end_product.category_id
    end_product.sub_category_id = product.sub_category_id
    return variant
