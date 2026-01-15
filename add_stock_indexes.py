#!/usr/bin/env python3
"""
Stock jadvalariga index qo'shish - Qidiruv tezligini oshirish
"""
import sys
from app import app, db
from sqlalchemy import text

def add_stock_indexes():
    """Stock jadvalariga index qo'shish"""
    with app.app_context():
        try:
            print("🔍 Mavjud index'larni tekshirish...")
            
            indexes_to_create = [
                {
                    'name': 'idx_store_stocks_product_store',
                    'table': 'store_stocks',
                    'columns': '(product_id, store_id)',
                    'description': 'Store stocks - product va store bo\'yicha qidiruv'
                },
                {
                    'name': 'idx_warehouse_stocks_product_warehouse',
                    'table': 'warehouse_stocks',
                    'columns': '(product_id, warehouse_id)',
                    'description': 'Warehouse stocks - product va warehouse bo\'yicha qidiruv'
                },
                {
                    'name': 'idx_products_barcode',
                    'table': 'products',
                    'columns': '(barcode)',
                    'description': 'Products - barcode bo\'yicha qidiruv',
                    'where': 'WHERE barcode IS NOT NULL'
                }
            ]
            
            created_count = 0
            skipped_count = 0
            
            for idx_info in indexes_to_create:
                index_name = idx_info['name']
                table_name = idx_info['table']
                columns = idx_info['columns']
                description = idx_info['description']
                where_clause = idx_info.get('where', '')
                
                # Index mavjudligini tekshirish
                check_query = text(f"""
                    SELECT indexname 
                    FROM pg_indexes 
                    WHERE tablename = :table_name 
                    AND indexname = :index_name
                """)
                
                result = db.session.execute(check_query, {
                    'table_name': table_name,
                    'index_name': index_name
                })
                
                if result.fetchone():
                    print(f"⏭️  Index mavjud: {index_name}")
                    skipped_count += 1
                    continue
                
                print(f"📊 Index qo'shilmoqda: {index_name}")
                print(f"   📋 {description}")
                
                # Index yaratish
                create_query = f"""
                    CREATE INDEX {index_name} 
                    ON {table_name}{columns}
                    {where_clause}
                """
                
                db.session.execute(text(create_query))
                db.session.commit()
                
                print(f"✅ Index yaratildi: {index_name}\n")
                created_count += 1
            
            print(f"\n{'='*60}")
            print(f"📊 Natija:")
            print(f"   ✅ Yaratilgan: {created_count}")
            print(f"   ⏭️  O'tkazib yuborilgan: {skipped_count}")
            print(f"{'='*60}")
            
            if created_count > 0:
                print("\n🚀 Yangi index'lar yaratildi!")
                print("📈 Qidiruv tezligi sezilarli darajada oshadi!")
                
                # Barcha index'larni ko'rsatish
                print("\n📋 Products jadvali index'lari:")
                list_query = text("""
                    SELECT 
                        indexname,
                        indexdef
                    FROM pg_indexes 
                    WHERE tablename IN ('products', 'store_stocks', 'warehouse_stocks')
                    ORDER BY tablename, indexname
                """)
                
                result = db.session.execute(list_query)
                for row in result:
                    print(f"   • {row[0]}")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Xatolik: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == '__main__':
    print("🚀 Stock Indexes Migration")
    print("=" * 60)
    add_stock_indexes()
    print("\n✅ Migration yakunlandi!")
