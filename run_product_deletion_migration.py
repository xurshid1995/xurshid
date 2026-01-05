#!/usr/bin/env python3
"""
Migration: Product o'chirish imkoniyatini berish
"""

from app import app, db
from sqlalchemy import text

def run_migration():
    with app.app_context():
        print("🟡 Migration boshlandi: Product o'chirish constraint'larini o'zgartirish...")
        
        # Migration SQL faylini o'qish
        with open('migrations/allow_product_deletion.sql', 'r', encoding='utf-8') as f:
            sql = f.read()
        
        # SQL statementlarni ajratish va bajarish
        statements = [stmt.strip() for stmt in sql.split(';') if stmt.strip() and not stmt.strip().startswith('--')]
        
        for i, stmt in enumerate(statements, 1):
            try:
                print(f"\n🔵 Statement {i}/{len(statements)} bajarilmoqda...")
                result = db.session.execute(text(stmt))
                db.session.commit()
                
                # Agar SELECT bo'lsa, natijani ko'rsatish
                if stmt.strip().upper().startswith('SELECT'):
                    rows = result.fetchall()
                    if rows:
                        print(f"   Constraint'lar:")
                        for row in rows:
                            print(f"   - {dict(row)}")
                    else:
                        print(f"   (Natija yo'q)")
                else:
                    print(f"   ✅ Muvaffaqiyatli")
            except Exception as e:
                print(f"   ⚠️ Xatolik (lekin davom etamiz): {e}")
                db.session.rollback()
        
        print("\n✅ Migration yakunlandi!")
        print("\n💡 Endi product o'chirilganda:")
        print("   - sale_items.product_id → NULL")
        print("   - transfer_items.product_id → NULL")
        print("   - Lekin notes'da mahsulot nomi saqlanadi!")

if __name__ == '__main__':
    run_migration()
