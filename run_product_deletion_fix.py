#!/usr/bin/env python3
"""
Migration: Product o'chirish - to'g'ri SQL
"""

from app import app, db
from sqlalchemy import text

def run_migration():
    with app.app_context():
        print("🟡 Migration boshlandi: product_id constraint'ini o'zgartirish...")
        
        try:
            # 1. NOT NULL constraint'ini olib tashlash
            print("\n🔵 1. sale_items.product_id NOT NULL olib tashlanmoqda...")
            db.session.execute(text("ALTER TABLE sale_items ALTER COLUMN product_id DROP NOT NULL"))
            db.session.commit()
            print("   ✅ Muvaffaqiyatli")
            
            # 2. Eski constraint'ni o'chirish
            print("\n🔵 2. Eski foreign key constraint o'chirilmoqda...")
            db.session.execute(text("ALTER TABLE sale_items DROP CONSTRAINT IF EXISTS sale_items_product_id_fkey"))
            db.session.commit()
            print("   ✅ Muvaffaqiyatli")
            
            # 3. Yangi constraint qo'shish (ON DELETE SET NULL)
            print("\n🔵 3. Yangi constraint qo'shilmoqda (ON DELETE SET NULL)...")
            db.session.execute(text("""
                ALTER TABLE sale_items 
                ADD CONSTRAINT sale_items_product_id_fkey 
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
            """))
            db.session.commit()
            print("   ✅ Muvaffaqiyatli")
            
            # 4. Tekshirish
            print("\n🔵 4. Tekshirish...")
            result = db.session.execute(text("""
                SELECT 
                    a.attname as column_name,
                    a.attnotnull as not_null
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = 'sale_items' 
                    AND a.attname = 'product_id'
            """))
            row = result.fetchone()
            if row:
                print(f"   Column: {row[0]}, NOT NULL: {row[1]}")
                if not row[1]:
                    print("   ✅ NOT NULL muvaffaqiyatli olib tashlandi!")
                else:
                    print("   ❌ NOT NULL hali ham mavjud!")
            
            print("\n✅ Migration yakunlandi!")
            print("\n💡 Endi product o'chirilganda sale_items.product_id NULL bo'ladi")
            
        except Exception as e:
            print(f"\n❌ Xatolik: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    run_migration()
