#!/usr/bin/env python3
"""Stock check items jadvali migratsiyasi"""

import psycopg2
from psycopg2 import sql
import os

# Database connection
DB_CONFIG = {
    'dbname': 'dokon_baza',
    'user': 'dokon_user',
    'password': 'dokon123',
    'host': 'localhost',
    'port': 5432
}

def run_migration():
    """Migratsiyani ishga tushirish"""
    try:
        # Database ga ulanish
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        print("🚀 Stock check items jadvali migratsiyasi boshlandi...")
        
        # Migration faylini o'qish
        migration_file = 'migrations/create_stock_check_items_table.sql'
        with open(migration_file, 'r', encoding='utf-8') as f:
            migration_sql = f.read()
        
        # Migratsiyani bajarish
        cur.execute(migration_sql)
        conn.commit()
        
        print("✅ Stock check items jadvali muvaffaqiyatli yaratildi!")
        
        # Jadval strukturasini tekshirish
        cur.execute("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'stock_check_items'
            ORDER BY ordinal_position;
        """)
        
        columns = cur.fetchall()
        print("\n📋 Jadval strukturasi:")
        for col in columns:
            print(f"  - {col[0]}: {col[1]}")
        
        # Mavjud ma'lumotlarni sanash
        cur.execute("SELECT COUNT(*) FROM stock_check_items;")
        count = cur.fetchone()[0]
        print(f"\n📊 Jami yozuvlar: {count}")
        
        cur.close()
        conn.close()
        
        print("\n✨ Migratsiya yakunlandi!")
        
    except Exception as e:
        print(f"❌ Xatolik: {e}")
        if conn:
            conn.rollback()
            conn.close()

if __name__ == '__main__':
    run_migration()
