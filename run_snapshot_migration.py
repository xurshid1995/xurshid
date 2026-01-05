#!/usr/bin/env python3
"""
Migration: product_name_snapshot ustuni qo'shish
"""

from app import app, db
from sqlalchemy import text

def run_migration():
    with app.app_context():
        print("🟡 Migration boshlandi: product_name_snapshot ustuni qo'shish...")
        
        # Migration SQL faylini o'qish
        with open('migrations/add_product_name_snapshot.sql', 'r', encoding='utf-8') as f:
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
                    for row in rows:
                        print(f"   {dict(row)}")
                else:
                    print(f"   ✅ Muvaffaqiyatli")
            except Exception as e:
                print(f"   ⚠️ Xatolik (lekin davom etamiz): {e}")
                db.session.rollback()
        
        print("\n✅ Migration yakunlandi!")

if __name__ == '__main__':
    run_migration()
