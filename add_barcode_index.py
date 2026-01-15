#!/usr/bin/env python3
"""
Barcode ustuniga index qo'shish - Qidiruv tezligini oshirish
"""
import sys
from app import app, db
from sqlalchemy import text

def add_barcode_index():
    """Barcode ustuniga index qo'shish"""
    with app.app_context():
        try:
            print("🔍 Mavjud index'larni tekshirish...")
            
            # Barcode index mavjudligini tekshirish
            check_query = text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'products' 
                AND indexname LIKE '%barcode%'
            """)
            
            result = db.session.execute(check_query)
            existing_indexes = [row[0] for row in result]
            
            if existing_indexes:
                print(f"✅ Barcode index allaqachon mavjud: {existing_indexes}")
                return
            
            print("📊 Barcode index qo'shilmoqda...")
            
            # Barcode ustuniga index qo'shish
            create_index = text("""
                CREATE INDEX idx_products_barcode 
                ON products(barcode) 
                WHERE barcode IS NOT NULL
            """)
            
            db.session.execute(create_index)
            db.session.commit()
            
            print("✅ Barcode index muvaffaqiyatli qo'shildi!")
            print("🚀 Endi barcode qidiruv 10-100 marta tezroq ishlaydi!")
            
            # Index ma'lumotlarini ko'rsatish
            info_query = text("""
                SELECT 
                    schemaname,
                    tablename,
                    indexname,
                    indexdef
                FROM pg_indexes 
                WHERE tablename = 'products' 
                AND indexname = 'idx_products_barcode'
            """)
            
            result = db.session.execute(info_query)
            for row in result:
                print(f"\n📋 Index ma'lumotlari:")
                print(f"   Schema: {row[0]}")
                print(f"   Table: {row[1]}")
                print(f"   Index: {row[2]}")
                print(f"   Definition: {row[3]}")
            
            # Jadvaldagi barcode'lar statistikasi
            stats_query = text("""
                SELECT 
                    COUNT(*) as total_products,
                    COUNT(barcode) as products_with_barcode,
                    COUNT(DISTINCT barcode) as unique_barcodes
                FROM products
            """)
            
            result = db.session.execute(stats_query)
            row = result.fetchone()
            print(f"\n📊 Mahsulotlar statistikasi:")
            print(f"   Jami mahsulotlar: {row[0]}")
            print(f"   Barcode'li mahsulotlar: {row[1]}")
            print(f"   Unikal barcode'lar: {row[2]}")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Xatolik: {e}")
            sys.exit(1)

if __name__ == '__main__':
    print("🚀 Barcode Index Migration")
    print("=" * 50)
    add_barcode_index()
    print("=" * 50)
    print("✅ Migration yakunlandi!")
