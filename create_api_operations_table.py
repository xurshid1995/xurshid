#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API operations jadvali - idempotency uchun
Bu jadval takroriy API so'rovlarni oldini olish uchun ishlatiladi
"""
import os
import sys
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_params = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'sayt_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres')
}

def create_table():
    """api_operations jadvalini yaratish"""
    try:
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()
        
        print("=" * 60)
        print("API OPERATIONS JADVALI YARATISH")
        print("=" * 60)
        print()
        
        # Jadval mavjudligini tekshirish
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'api_operations'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if table_exists:
            print("⚠️  api_operations jadvali allaqachon mavjud")
            print()
            response = input("Jadvalni qayta yaratishni xohlaysizmi? (ha/yo'q): ").lower()
            if response not in ['ha', 'yes', 'y']:
                print("❌ Bekor qilindi")
                cursor.close()
                conn.close()
                return
            
            # Eski jadvalni o'chirish
            print("🗑️  Eski jadvalni o'chirish...")
            cursor.execute("DROP TABLE IF EXISTS api_operations CASCADE;")
            conn.commit()
            print("✅ Eski jadval o'chirildi")
        
        # Jadval yaratish
        print("📝 Yangi jadval yaratilmoqda...")
        cursor.execute("""
            CREATE TABLE api_operations (
                id SERIAL PRIMARY KEY,
                idempotency_key VARCHAR(100) UNIQUE NOT NULL,
                operation_type VARCHAR(50) NOT NULL,
                user_id INTEGER REFERENCES users(id),
                status VARCHAR(20) DEFAULT 'completed',
                result_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Index'lar yaratish
        print("📊 Index'lar yaratilmoqda...")
        cursor.execute("""
            CREATE INDEX idx_api_operations_idempotency_key 
            ON api_operations(idempotency_key);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_api_operations_created_at 
            ON api_operations(created_at);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_api_operations_operation_type 
            ON api_operations(operation_type);
        """)
        
        conn.commit()
        
        print("✅ api_operations jadvali yaratildi")
        print("✅ Index'lar yaratildi")
        print()
        
        # Jadval strukturasini ko'rsatish
        cursor.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns 
            WHERE table_name = 'api_operations'
            ORDER BY ordinal_position;
        """)
        
        print("📋 Jadval strukturasi:")
        print("-" * 60)
        print(f"{'Ustun nomi':<25} {'Turi':<20} {'NULL?':<10} {'Default'}")
        print("-" * 60)
        for row in cursor.fetchall():
            col_name, data_type, nullable, default = row
            null_str = 'NULL' if nullable == 'YES' else 'NOT NULL'
            default_str = str(default)[:20] if default else '-'
            print(f"{col_name:<25} {data_type:<20} {null_str:<10} {default_str}")
        
        print("-" * 60)
        print()
        
        # Index'larni ko'rsatish
        cursor.execute("""
            SELECT indexname, indexdef 
            FROM pg_indexes 
            WHERE tablename = 'api_operations';
        """)
        
        print("📊 Index'lar:")
        print("-" * 60)
        for row in cursor.fetchall():
            index_name, index_def = row
            print(f"  • {index_name}")
        print("-" * 60)
        print()
        
        # Tozalash uchun function yaratish (eski ma'lumotlarni o'chirish)
        print("🧹 Tozalash funksiyasini yaratish...")
        cursor.execute("""
            CREATE OR REPLACE FUNCTION cleanup_old_api_operations()
            RETURNS INTEGER AS $$
            DECLARE
                deleted_count INTEGER;
            BEGIN
                -- 30 kundan eski operatsiyalarni o'chirish
                DELETE FROM api_operations 
                WHERE created_at < NOW() - INTERVAL '30 days';
                
                GET DIAGNOSTICS deleted_count = ROW_COUNT;
                RETURN deleted_count;
            END;
            $$ LANGUAGE plpgsql;
        """)
        conn.commit()
        print("✅ Tozalash funksiyasi yaratildi: cleanup_old_api_operations()")
        print()
        
        cursor.close()
        conn.close()
        
        print("=" * 60)
        print("✅ MIGRATION MUVAFFAQIYATLI YAKUNLANDI")
        print("=" * 60)
        print()
        print("📝 Qo'shimcha ma'lumot:")
        print("  - Jadval: api_operations")
        print("  - Maqsad: Takroriy API so'rovlarni oldini olish (idempotency)")
        print("  - Tozalash: SELECT cleanup_old_api_operations();")
        print("  - 30 kundan eski ma'lumotlar avtomatik o'chiriladi")
        print()
        
    except Exception as e:
        print(f"\n❌ Xatolik: {e}")
        import traceback
        traceback.print_exc()
        if 'conn' in locals():
            conn.rollback()

if __name__ == '__main__':
    create_table()
