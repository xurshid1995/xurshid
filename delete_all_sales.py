#!/usr/bin/env python3
"""
⚠️ XAVFLI: Barcha savdolarni o'chirish
"""

from app import app, db
from sqlalchemy import text

def delete_all_sales():
    with app.app_context():
        print("⚠️ BARCHA SAVDOLARNI O'CHIRISH BOSHLANDI...")
        print("=" * 60)
        
        # Avval sanash
        result = db.session.execute(text("SELECT COUNT(*) FROM sales"))
        total_sales = result.scalar()
        
        result = db.session.execute(text("SELECT COUNT(*) FROM sale_items"))
        total_items = result.scalar()
        
        print(f"📊 Jami savdolar: {total_sales}")
        print(f"📊 Jami sale items: {total_items}")
        print("=" * 60)
        
        if total_sales == 0:
            print("✅ Database bo'sh - o'chirish kerak emas")
            return
        
        try:
            # 1. Sale items o'chirish
            print("\n🔴 1. Sale items o'chirilmoqda...")
            db.session.execute(text("DELETE FROM sale_items"))
            db.session.commit()
            print("   ✅ Sale items o'chirildi")
            
            # 2. Sales o'chirish
            print("\n🔴 2. Sales o'chirilmoqda...")
            db.session.execute(text("DELETE FROM sales"))
            db.session.commit()
            print("   ✅ Sales o'chirildi")
            
            # 3. Auto-increment reset (optional)
            print("\n🔄 3. ID sequence reset qilinmoqda...")
            db.session.execute(text("ALTER SEQUENCE sales_id_seq RESTART WITH 1"))
            db.session.execute(text("ALTER SEQUENCE sale_items_id_seq RESTART WITH 1"))
            db.session.commit()
            print("   ✅ Sequence reset bo'ldi")
            
            print("\n" + "=" * 60)
            print("✅ BARCHA SAVDOLAR O'CHIRILDI!")
            print(f"   - {total_sales} ta savdo")
            print(f"   - {total_items} ta sale item")
            print("=" * 60)
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ XATOLIK: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    print("\n" + "🚨" * 30)
    print("⚠️  OXIRGI TASDIQLASH!")
    print("🚨" * 30)
    print("\nBu operatsiya BUTUN SAVDO TARIXINI O'CHIRADI!")
    print("Bu operatsiyani QAYTARIB BO'LMAYDI!\n")
    
    confirm = input("Davom etish uchun 'DELETE ALL' deb yozing: ")
    
    if confirm == "DELETE ALL":
        delete_all_sales()
    else:
        print("\n❌ Bekor qilindi - savdolar saqlanadi")
