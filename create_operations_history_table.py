#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Amaliyotlar tarixi jadvali - Barcha tizim amaliyotlarini saqlash
"""
import os
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
    """operations_history jadvalini yaratish"""
    try:
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()
        
        print("=" * 60)
        print("AMALIYOTLAR TARIXI JADVALI YARATISH")
        print("=" * 60)
        print()
        
        # Jadval mavjudligini tekshirish
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'operations_history'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if table_exists:
            print("⚠️  operations_history jadvali allaqachon mavjud")
            print()
            response = input("Jadvalni qayta yaratishni xohlaysizmi? (ha/yo'q): ").lower()
            if response not in ['ha', 'yes', 'y']:
                print("❌ Bekor qilindi")
                cursor.close()
                conn.close()
                return
            
            print("🗑️  Eski jadvalni o'chirish...")
            cursor.execute("DROP TABLE IF EXISTS operations_history CASCADE;")
            conn.commit()
            print("✅ Eski jadval o'chirildi")
        
        # Jadval yaratish
        print("📝 Yangi jadval yaratilmoqda...")
        cursor.execute("""
            CREATE TABLE operations_history (
                id SERIAL PRIMARY KEY,
                operation_type VARCHAR(50) NOT NULL,
                table_name VARCHAR(50),
                record_id INTEGER,
                user_id INTEGER REFERENCES users(id),
                username VARCHAR(100),
                description TEXT,
                old_data JSONB,
                new_data JSONB,
                ip_address VARCHAR(50),
                location_id INTEGER,
                location_type VARCHAR(20),
                location_name VARCHAR(200),
                amount DECIMAL(15, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Index'lar yaratish
        print("📊 Index'lar yaratilmoqda...")
        cursor.execute("""
            CREATE INDEX idx_operations_history_user_id ON operations_history(user_id);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_operations_history_created_at ON operations_history(created_at);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_operations_history_operation_type ON operations_history(operation_type);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_operations_history_table_name ON operations_history(table_name);
        """)
        
        cursor.execute("""
            CREATE INDEX idx_operations_history_location ON operations_history(location_id, location_type);
        """)
        
        conn.commit()
        
        print("✅ operations_history jadvali yaratildi")
        print("✅ Index'lar yaratildi")
        print()
        
        # Jadval strukturasini ko'rsatish
        cursor.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns 
            WHERE table_name = 'operations_history'
            ORDER BY ordinal_position;
        """)
        
        print("📋 Jadval strukturasi:")
        print("-" * 80)
        print(f"{'Ustun nomi':<25} {'Turi':<20} {'NULL?':<10} {'Default'}")
        print("-" * 80)
        for row in cursor.fetchall():
            col_name, data_type, nullable, default = row
            null_str = 'NULL' if nullable == 'YES' else 'NOT NULL'
            default_str = str(default)[:30] if default else '-'
            print(f"{col_name:<25} {data_type:<20} {null_str:<10} {default_str}")
        
        print("-" * 80)
        print()
        
        # Tozalash funksiyasini yaratish
        print("🧹 Tozalash funksiyasini yaratish...")
        cursor.execute("""
            CREATE OR REPLACE FUNCTION cleanup_old_operations_history()
            RETURNS INTEGER AS $$
            DECLARE
                deleted_count INTEGER;
            BEGIN
                -- 90 kundan eski operatsiyalarni o'chirish
                DELETE FROM operations_history 
                WHERE created_at < NOW() - INTERVAL '90 days';
                
                GET DIAGNOSTICS deleted_count = ROW_COUNT;
                RETURN deleted_count;
            END;
            $$ LANGUAGE plpgsql;
        """)
        conn.commit()
        print("✅ Tozalash funksiyasi yaratildi: cleanup_old_operations_history()")
        print()
        
        # Test ma'lumot qo'shish
        print("📝 Test ma'lumot qo'shilmoqda...")
        cursor.execute("""
            INSERT INTO operations_history 
            (operation_type, table_name, user_id, username, description, location_name, amount)
            VALUES 
            ('system', 'operations_history', 1, 'admin', 'Amaliyotlar tarixi jadvali yaratildi', 'Sistema', 0);
        """)
        conn.commit()
        print("✅ Test ma'lumot qo'shildi")
        print()
        
        cursor.close()
        conn.close()
        
        print("=" * 60)
        print("✅ MIGRATION MUVAFFAQIYATLI YAKUNLANDI")
        print("=" * 60)
        print()
        print("📝 Qo'shimcha ma'lumot:")
        print("  - Jadval: operations_history")
        print("  - Maqsad: Barcha tizim amaliyotlarini saqlash va kuzatish")
        print("  - Tozalash: SELECT cleanup_old_operations_history();")
        print("  - 90 kundan eski ma'lumotlar tozalash funksiyasi bilan o'chiriladi")
        print()
        
    except Exception as e:
        print(f"\n❌ Xatolik: {e}")
        import traceback
        traceback.print_exc()
        if 'conn' in locals():
            conn.rollback()

if __name__ == '__main__':
    create_table()
