#!/usr/bin/env python3
"""
Transfer jadvalidagi product_id foreign key ga CASCADE qo'shish
"""

from app import app, db

def run_migration():
    """Migration ni bajarish"""
    with app.app_context():
        try:
            # Eski constraint ni o'chirish
            db.session.execute(db.text("""
                ALTER TABLE transfers DROP CONSTRAINT IF EXISTS transfers_product_id_fkey;
            """))
            print("✅ Eski constraint o'chirildi")
            
            # Yangi constraint CASCADE bilan qo'shish
            db.session.execute(db.text("""
                ALTER TABLE transfers 
                ADD CONSTRAINT transfers_product_id_fkey 
                FOREIGN KEY (product_id) 
                REFERENCES products(id) 
                ON DELETE CASCADE;
            """))
            print("✅ Yangi constraint CASCADE bilan qo'shildi")
            
            db.session.commit()
            print("\n✅ Migration muvaffaqiyatli bajarildi!")
            print("Endi Product o'chirilganda, unga tegishli transferlar ham avtomatik o'chiriladi.")
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Xatolik: {e}")

if __name__ == '__main__':
    run_migration()
