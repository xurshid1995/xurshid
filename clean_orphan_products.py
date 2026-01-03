#!/usr/bin/env python3
"""
Yetim mahsulotlarni tozalash scripti
Hech qanday do'kon yoki omborda mavjud bo'lmagan mahsulotlarni o'chiradi
"""

from app import app, db, Product, StoreStock, WarehouseStock
from sqlalchemy import and_, not_, exists

def clean_orphan_products():
    """Yetim mahsulotlarni topish va o'chirish"""
    with app.app_context():
        # Yetim mahsulotlarni topish
        orphan_products = Product.query.filter(
            and_(
                not_(exists().where(StoreStock.product_id == Product.id)),
                not_(exists().where(WarehouseStock.product_id == Product.id))
            )
        ).all()
        
        if not orphan_products:
            print("✅ Yetim mahsulotlar yo'q!")
            return
        
        print(f"\n⚠️  {len(orphan_products)} ta yetim mahsulot topildi:\n")
        
        for product in orphan_products:
            print(f"  - ID: {product.id}, Nomi: {product.name}")
        
        # Tasdiqlash
        print(f"\n❓ Bu mahsulotlarni o'chirmoqchimisiz? (ha/yo'q): ", end='')
        confirm = input().strip().lower()
        
        if confirm in ['ha', 'yes', 'y']:
            # O'chirish
            for product in orphan_products:
                db.session.delete(product)
            
            db.session.commit()
            print(f"\n✅ {len(orphan_products)} ta yetim mahsulot o'chirildi!")
        else:
            print("\n❌ Bekor qilindi.")

if __name__ == '__main__':
    clean_orphan_products()
