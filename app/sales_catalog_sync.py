"""
Keeps Setup > End Products (app/setup_routes.py, used by FMS/Delegations
linked entities) in sync with the Sales Catalog's ProductVariant rows.
Catalog is the authoritative place variants are *created* (it has category/
sub-category/attributes); End Products is a lighter mirror consumed
elsewhere, matched on (tenant_id, sku_code).

Split into its own module (rather than living in sales_catalog.py or
setup_routes.py) to avoid an import cycle between those two files.
"""
from sqlalchemy.orm import Session

from .database import EndProduct, ProductVariant


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

    if end_product:
        end_product.name = name
        end_product.sku_code = variant.sku_code
        end_product.unit = unit_abbr
        end_product.is_active = variant.is_active
    else:
        end_product = EndProduct(
            tenant_id=variant.tenant_id, name=name, sku_code=variant.sku_code,
            unit=unit_abbr, created_by_id=variant.created_by_id,
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
