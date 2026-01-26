#!/usr/bin/env python3
"""
Database Clear Script
Ma'lumotlar bazasidagi barcha ma'lumotlarni o'chiradi (struktura saqlanadi)
"""

from app import (
    app, db,
    Sale, SaleItem, Customer, Product, Store, Warehouse, 
    StoreStock, WarehouseStock, Transfer, DebtPayment,
    OperationHistory, ProductAddHistory, User, CurrencyRate,
    StockCheckSession, StockCheckItem
)

def clear_database():
    """Ma'lumotlar bazasini tozalash"""
    with app.app_context():
        try:
            print("⚠️  DIQQAT! Ma'lumotlar bazasi tozalanmoqda...")
            print("=" * 60)
            
            # Barcha jadvallarni tartib bilan o'chirish (foreign key constraint'lar tufayli)
            tables_to_clear = [
                (StockCheckItem, "Stock Check Items"),
                (StockCheckSession, "Stock Check Sessions"),
                (DebtPayment, "Debt Payments"),
                (SaleItem, "Sale Items"),
                (Sale, "Sales"),
                (Transfer, "Transfers"),
                (ProductAddHistory, "Product Add History"),
                (OperationHistory, "Operation History"),
                (StoreStock, "Store Stock"),
                (WarehouseStock, "Warehouse Stock"),
                (Product, "Products"),
                (Customer, "Customers"),
                # Store va Warehouse user'larga bog'langan, shuning uchun o'chirilmaydi
                # (Store, "Stores"),
                # (Warehouse, "Warehouses"),
                (CurrencyRate, "Currency Rates"),
                # User jadvali saqlanadi - hech qanday user o'chirilmaydi
            ]
            
            deleted_counts = {}
            
            for model, name in tables_to_clear:
                count = model.query.count()
                if count > 0:
                    model.query.delete()
                    deleted_counts[name] = count
                    print(f"✓ {name}: {count} ta yozuv o'chirildi")
                else:
                    print(f"- {name}: Bo'sh")
            
            # User jadvali saqlanadi
            print(f"✓ Users: Saqlanib qoldi (o'chirilmadi)")
            
            # O'zgarishlarni saqlash
            db.session.commit()
            
            print("=" * 60)
            print("✅ Ma'lumotlar bazasi muvaffaqiyatli tozalandi!")
            print("\n📊 Jami o'chirilgan yozuvlar:")
            total = sum(deleted_counts.values())
            for table, count in deleted_counts.items():
                print(f"   • {table}: {count}")
            print(f"\n   JAMI: {total} ta yozuv")
            
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Xatolik yuz berdi: {str(e)}")
            return False

if __name__ == "__main__":
    print("🗑️  DATABASE CLEAR SCRIPT")
    print("=" * 60)
    print("⚠️  DIQQAT! Bu script barcha ma'lumotlarni o'chiradi!")
    print("   - Barcha savdolar")
    print("   - Barcha mijozlar")
    print("   - Barcha mahsulotlar")
    print("   - Barcha qarzlar")
    print("   - Barcha to'lovlar")
    print("   ⚠️  User'lar SAQLANADI (o'chirilmaydi)")
    print("   ⚠️  Dokon va Omborlar SAQLANADI (o'chirilmaydi)")
    print("=" * 60)
    
    confirmation = input("\n❓ Davom etishni xohlaysizmi? (ha/yo'q): ").strip().lower()
    
    if confirmation in ['ha', 'yes', 'y']:
        double_check = input("❗ Tasdiqlash uchun 'TOZALASH' so'zini kiriting: ").strip()
        
        if double_check == 'TOZALASH':
            print("\n🚀 Jarayon boshlandi...\n")
            success = clear_database()
            
            if success:
                print("\n✨ Tayyor! Database tozalandi va ishlatishga tayyor.")
            else:
                print("\n💥 Xatolik! Database tozalanmadi.")
        else:
            print("\n❌ Noto'g'ri tasdiqlash kodi. Bekor qilindi.")
    else:
        print("\n❌ Bekor qilindi. Hech narsa o'zgartirilmadi.")
