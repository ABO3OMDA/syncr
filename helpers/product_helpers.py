import json
import base64
import os
from datetime import datetime
from helpers import sql_connector
from helpers.helpers import slugify
from helpers.odoo_connector import OdooConnector
from helpers.sql_connector import SQLConnector


class ProductHelper:

    connector: OdooConnector
    sql_connector: SQLConnector

    def __init__(self, connector: OdooConnector, sql_connector: SQLConnector):
        self.connector = connector
        self.sql_connector = sql_connector
        
    def get_product_tax_info(self, product_template_id):
        """Get tax information for a product template"""
        try:
            # Get product template with tax info
            product_data = self.connector.read(
                "product.template", 
                [product_template_id], 
                ["taxes_id", "list_price"]
            )
            
            if not product_data:
                return {"has_tax": False, "tax_rate": 0, "tax_amount": 0}
            
            product = product_data[0]
            tax_info = {"has_tax": False, "tax_rate": 0, "tax_amount": 0}
            
            # Get tax details if taxes exist
            if product.get("taxes_id"):
                tax_ids = product["taxes_id"]
                taxes = self.connector.read("account.tax", tax_ids, ["name", "amount", "amount_type"])
                
                total_tax_rate = 0
                for tax in taxes:
                    if tax["amount_type"] == "percent":
                        total_tax_rate += tax["amount"]
                
                if total_tax_rate > 0:
                    tax_info["has_tax"] = True
                    tax_info["tax_rate"] = total_tax_rate
                    
                    # Calculate tax amount (assuming price is tax-inclusive)
                    price_with_tax = product["list_price"]
                    price_without_tax = price_with_tax / (1 + (total_tax_rate / 100))
                    tax_info["tax_amount"] = price_with_tax - price_without_tax
            
            return tax_info
            
        except Exception as e:
            print(f"Error getting tax info for product {product_template_id}: {str(e)}")
            return {"has_tax": False, "tax_rate": 0, "tax_amount": 0}

    def get_odoo_image_url(self, product_id, image_type='main'):
        """Generate Odoo public image URL - with correct naming convention"""
        try:
            if image_type == 'main':
                # Main product cover image uses image_1920
                image_url = f"https://odoo.eboutiques.com/public/product_image/{product_id}/image_1920"
            elif image_type.startswith('image_'):
                # Gallery images use image_1, image_2, etc.
                image_url = f"https://odoo.eboutiques.com/public/product_image/{product_id}/{image_type}"
            else:
                # For numbered gallery images
                image_url = f"https://odoo.eboutiques.com/public/product_image/{product_id}/image_{image_type}"
            
            print(f"🔗 Generated Odoo image URL: {image_url}")
            return image_url
            
        except Exception as e:
            print(f"❌ Failed to generate image URL for product {product_id}: {str(e)}")
            return "no_product_image.jpg"

    def sync_product_gallery(self, product_template_id, laravel_product_id):
        """Sync product gallery URLs from Odoo to Laravel - Fixed with correct image naming"""
        try:
            print(f"🖼️  Syncing gallery for product {product_template_id}")
            
            # Clear existing gallery for this product (from sync)
            self.sql_connector.delete(
                "product_galleries", 
                f"`product_id` = '{laravel_product_id}' AND `image` LIKE '%odoo.eboutiques.com%'"
            )
            
            synced_count = 0
            
            # First, ensure main image is updated with image_1920
            main_image_url = f"https://odoo.eboutiques.com/public/product_image/{product_template_id}/image_1920"
            print(f"\n  🖼️  Updating main cover image: {main_image_url}")
            
            self.sql_connector.update(
                "products",
                f"`id` = '{laravel_product_id}'",
                {"thumb_image": main_image_url}
            )
            print(f"  ✅ Main cover image updated")
            
            # Now sync gallery images (image_1, image_2, etc.)
            # These are separate from the main image_1920
            print(f"\n  📸 Syncing gallery images...")
            
            for i in range(1, 11):  # Check up to 10 gallery images
                # Gallery images use image_1, image_2, etc. (NOT image_1920)
                gallery_image_url = f"https://odoo.eboutiques.com/public/product_image/{product_template_id}/image_{i}"
                
                print(f"  🔍 Adding gallery image_{i}")
                
                # Insert URL directly - Laravel ImageHelper handles validation
                gallery_data = {
                    "product_id": laravel_product_id,
                    "image": gallery_image_url,
                    "status": 1,
                    "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                try:
                    result = self.sql_connector.insert("product_galleries", gallery_data).fetch()
                    if result:
                        synced_count += 1
                        print(f"    ✅ Gallery image_{i} stored: {gallery_image_url}")
                    else:
                        print(f"    ⚠️  Failed to insert gallery image_{i}")
                        # If we've synced some images but this one failed, 
                        # it might mean there are no more images
                        if synced_count > 0 and i > synced_count + 2:
                            print(f"    ℹ️  Stopping - likely no more gallery images after {synced_count}")
                            break
                except Exception as e:
                    # Might be duplicate or no more images, skip
                    print(f"    ⚠️  Could not insert image_{i}: {str(e)}")
                    # If we've already found some images and get errors, 
                    # it probably means no more images exist
                    if synced_count > 0 and "Duplicate" not in str(e):
                        print(f"    ℹ️  Stopping - likely no more gallery images")
                        break
                    continue
            
            print(f"\n📊 Gallery sync summary:")
            print(f"  - Main cover image: image_1920 ✅")
            print(f"  - Gallery images synced: {synced_count} (image_1 to image_{synced_count})")
            print(f"🎉 Total: 1 main image + {synced_count} gallery images")
            
            return synced_count
            
        except Exception as e:
            print(f"❌ Gallery sync failed for product {product_template_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            return 0

    def upsert_product_variant(self, v, attrs, product_id, tax_info, template_id=None):
        """Updated variant upsert with tax information and template_id for images"""
        if (
            v["default_code"] is False
            or v["default_code"] is None
            or v["default_code"] == "False"
            or v["default_code"] == ""
        ):
            print(f"⚠️  Skipping variant {v.get('display_name', 'Unknown')} - no SKU")
            return None

        details = []
        for a in attrs:
            details.append(
                {
                    "id": a["id"],
                    "name": a["html_color"] if a["html_color"] else a["name"],
                    "type": "Color" if a["html_color"] else "Text",
                    "typeName": a["attribute_line_id"][1],
                    "isActive": 1,
                }
            )

        details = sorted(details, key=lambda k: k["type"])

        if len(details) == 0:
            name = v["display_name"] if v["display_name"] is not None else "default"
            details = [
                {
                    "id": v["id"],
                    "name": name,
                    "type": "Text",
                    "typeName": name,
                    "isActive": 0,
                }
            ]

        # Calculate variant price considering tax
        variant_price_with_tax = v["lst_price"]
        if tax_info["has_tax"]:
            variant_price_without_tax = variant_price_with_tax / (1 + (tax_info["tax_rate"] / 100))
            variant_tax_amount = variant_price_with_tax - variant_price_without_tax
        else:
            variant_price_without_tax = variant_price_with_tax
            variant_tax_amount = 0

        # Handle variant image - use Odoo public URL directly
        variant_image = "no_product_image.jpg"
        if v.get('image_1920'):
            # Use template_id if provided, otherwise fall back to variant id
            image_id = template_id if template_id else v['id']
            variant_image = f"https://odoo.eboutiques.com/public/product_image/{image_id}/image_1920"
            print(f"🔗 Using variant image URL: {variant_image}")

        # Prepare variant data
        variant_data = {
            "name": v["display_name"],
            "product_id": product_id,
            "sku": v["default_code"],
            "stock": v["qty_available"],
            "price": variant_price_with_tax,
            "price_without_tax": round(variant_price_without_tax, 2),
            "cost_price": v["standard_price"],
            "tax_rate": tax_info["tax_rate"],
            "tax_amount": round(variant_tax_amount, 2),
            "tax_inclusive": 1,
            "percentage": (
                0
                if variant_price_with_tax == 0
                else round(v["standard_price"] / variant_price_with_tax * 100, 2)
            ),
            "weight": v["weight"] * 1000,
            "details": json.dumps(details),
            "status": 1 if v.get("active", True) else 0,
            "remote_key_id": str(v["id"]),
            "image": variant_image  # Add variant image URL
        }

        # Enhanced update data with all current values
        update_data = {
            "stock": v["qty_available"],
            "price": variant_price_with_tax,
            "price_without_tax": round(variant_price_without_tax, 2),
            "cost_price": v["standard_price"],
            "tax_rate": tax_info["tax_rate"],
            "tax_amount": round(variant_tax_amount, 2),
            "details": json.dumps(details),
            "name": v["display_name"],
            "status": 1 if v.get("active", True) else 0,
            "weight": v["weight"] * 1000,
            "image": variant_image  # Add variant image URL
        }

        try:
            sqlProdVariant = (
                self.sql_connector
                .upsert(
                    "product_variants",
                    variant_data,
                    updatedData=update_data,
                    where_clause=" `remote_key_id` = '%s' " % str(v["id"]),
                )
                .fetch()
            )
            
            if sqlProdVariant:
                print(f"  ✅ Variant synced: {v['display_name']} (SKU: {v['default_code']})")
            
            return sqlProdVariant
            
        except Exception as e:
            print(f"  ❌ Variant sync failed for {v.get('display_name', 'Unknown')}: {str(e)}")
            return None

    def upsert_product_template(self, p, variants, attrs):
        """Updated product template upsert with tax information"""
        print(f"\n{'='*60}")
        print(f"📦 Processing: {p['name']}")
        print(f"🆔 Odoo ID: {p['id']}")
        print(f"💰 Price: {p['list_price']}")
        print(f"📊 Stock: {p['qty_available']}")

        # Get tax information for this product
        tax_info = self.get_product_tax_info(p["id"])
        print(f"🏷️  Tax: {tax_info['tax_rate']}% (Amount: {tax_info['tax_amount']:.2f})")

        # Calculate product price considering tax
        product_price_with_tax = p["list_price"]
        if tax_info["has_tax"]:
            product_price_without_tax = product_price_with_tax / (1 + (tax_info["tax_rate"] / 100))
            product_tax_amount = product_price_with_tax - product_price_without_tax
        else:
            product_price_without_tax = product_price_with_tax
            product_tax_amount = 0

        # Handle product image - use Odoo public URL directly
        product_image = f"https://odoo.eboutiques.com/public/product_image/{p['id']}/image_1920"
        print(f"🔗 Using Odoo main image URL: {product_image}")

        # Generate unique slug
        slug_base = slugify(p["name"])
        timestamp = int(datetime.now().timestamp())
        slug = f"{slug_base}-{p['id']}-{timestamp}"

        # Prepare product data
        product_data = {
            "name": p["name"],
            "short_name": p["name"][:100] if len(p["name"]) > 100 else p["name"],
            "slug": slug,
            "sku": p.get("default_code", ""),
            "qty": p["qty_available"],
            "thumb_image": product_image,
            "category_id": 12,
            "sub_category_id": 10,
            "child_category_id": 0,
            "weight": p["weight"] * 1000,
            "seo_title": p["name"],
            "seo_description": p["name"],
            "price": product_price_with_tax,
            "price_without_tax": round(product_price_without_tax, 2),
            "tax_rate": tax_info["tax_rate"],
            "tax_amount": round(product_tax_amount, 2),
            "tax_inclusive": 1,
            "short_description": p["name"],
            "long_description": p["name"],
            "status": 1 if p.get("active", True) else 0,
            "approve_by_admin": 1,  # Auto-approve synced products
            "uuid": "o_imported_%s" % p["id"],
            "remote_key_id": str(p["id"]),
        }

        # Enhanced update data - sync all current values from Odoo
        update_data = {
            "name": p["name"],
            "short_name": p["name"][:100] if len(p["name"]) > 100 else p["name"],
            "qty": p["qty_available"],
            "price": product_price_with_tax,
            "price_without_tax": round(product_price_without_tax, 2),
            "tax_rate": tax_info["tax_rate"],
            "tax_amount": round(product_tax_amount, 2),
            "thumb_image": product_image,
            "weight": p["weight"] * 1000,
            "status": 1 if p.get("active", True) else 0,
            "remote_key_id": str(p["id"]),
        }

        try:
            sqlProd = (
                self.sql_connector
                .upsert(
                    "products",
                    product_data,
                    updatedData=update_data,
                    where_clause=" `remote_key_id` = '%s' " % str(p["id"]),
                )
                .fetch()
            )

            if sqlProd is None:
                print("⚠️  Product sync returned None - checking existing record...")
                existing = self.sql_connector.getOne("products", f"`remote_key_id` = '{p['id']}'").fetch()
                if existing:
                    print(f"✅ Product exists with Laravel ID: {existing['id']}")
                    sqlProd = existing
                else:
                    print("❌ ERROR: Product not found after upsert!")
                    return None

            if not sqlProd.get("id"):
                print("❌ Product sync failed - no ID returned")
                return None

            product_laravel_id = sqlProd["id"]
            print(f"✅ Product synced to Laravel ID: {product_laravel_id}")

            # Update variants with tax and image information
            synced_variants = 0
            variant_errors = 0
            
            print(f"🔧 Processing {len(variants)} variants...")
            
            for v in variants:
                if v.get("default_code") and v["default_code"]:  # Only sync variants with SKU
                    related_attr = [
                        a for a in attrs if a["id"] in v["product_template_variant_value_ids"]
                    ]
                    related_attr = list({a["id"]: a for a in related_attr}.values())
                    
                    variant_result = self.upsert_product_variant(v, related_attr, product_laravel_id, tax_info, p['id'])
                    if variant_result:
                        synced_variants += 1
                    else:
                        variant_errors += 1

            print(f"✅ Synced {synced_variants} variants ({variant_errors} errors)")

            # Cleanup obsolete variants (simplified to avoid syntax issues)
            if synced_variants > 0:
                try:
                    current_skus = [v["default_code"] for v in variants if v.get("default_code")]
                    if current_skus:
                        # Build a safe query to disable old variants
                        sku_list = "', '".join(current_skus)
                        cleanup_query = f"`product_id` = '{product_laravel_id}' AND `sku` NOT IN ('{sku_list}')"
                        self.sql_connector.update("product_variants", cleanup_query, {"status": 0})
                        print("✅ Cleaned up obsolete variants")
                except Exception as e:
                    print(f"⚠️  Variant cleanup warning: {str(e)}")

            # Sync product gallery (multiple images)
            gallery_count = self.sync_product_gallery(p["id"], product_laravel_id)

            print(f"📊 Summary:")
            print(f"  - Laravel ID: {product_laravel_id}")
            print(f"  - Price: {product_price_with_tax} (includes {tax_info['tax_rate']}% tax)")
            print(f"  - Stock: {p['qty_available']}")
            print(f"  - Image: {product_image}")
            print(f"  - Variants: {synced_variants}")
            print(f"  - Gallery Images: {gallery_count}")
            print("✅ Product sync completed successfully")
            
            return sqlProd

        except Exception as e:
            print(f"❌ Product sync failed for {p.get('name', 'Unknown')}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None