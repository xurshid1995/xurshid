#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Savdo ma'lumotlarini tekshirish skripti
"""

from app import db, Sale, SaleItem, Product, Customer, Store, Warehouse
from sqlalchemy import func

def check_sales():
    print("=" * 80)
    print("🔍 SAVDO MA'LUMOTLARI TEKSHIRUVI")
    print("=" * 80)
    
    # 1. Umumiy statistika
    total_sales = Sale.query.count()
    paid_sales = Sale.query.filter_by(payment_status='paid').count()
    pending_sales = Sale.query.filter_by(payment_status='pending').count()
    
    print(f"\n📊 UMUMIY STATISTIKA:")
    print(f"   Jami savdolar: {total_sales} ta")
    print(f"   To'langan savdolar: {paid_sales} ta")
    print(f"   Pending savdolar: {pending_sales} ta")
    
    if total_sales == 0:
        print("\n❌ Hech qanday savdo topilmadi!")
        return
    
    # 2. Jami summa va foyda
    stats = db.session.query(
        func.sum(Sale.total_amount).label('total_revenue'),
        func.sum(Sale.total_profit).label('total_profit'),
        func.sum(Sale.total_cost).label('total_cost')
    ).filter(Sale.payment_status == 'paid').first()
    
    print(f"\n💰 MOLIYAVIY STATISTIKA:")
    print(f"   Jami daromad: ${float(stats.total_revenue or 0):,.2f}")
    print(f"   Jami foyda: ${float(stats.total_profit or 0):,.2f}")
    print(f"   Jami tan narx: ${float(stats.total_cost or 0):,.2f}")
    
    # 3. So'nggi 5 ta savdo
    print(f"\n📋 SO'NGGI 5 TA SAVDO:")
    print("-" * 80)
    
    latest_sales = Sale.query.order_by(Sale.sale_date.desc()).limit(5).all()
    
    for i, sale in enumerate(latest_sales, 1):
        print(f"\n{i}. SAVDO ID: {sale.id}")
        print(f"   Sana: {sale.sale_date}")
        print(f"   Mijoz: {sale.customer.name if sale.customer else 'Noma\'lum'}")
        print(f"   Sotuvchi: {sale.created_by}")
        
        # Store yoki warehouse
        if sale.location_type == 'store' and sale.location_id:
            store = Store.query.get(sale.location_id)
            location_name = f"Do'kon: {store.name}" if store else f"Do'kon ID: {sale.location_id}"
        elif sale.location_type == 'warehouse' and sale.location_id:
            warehouse = Warehouse.query.get(sale.location_id)
            location_name = f"Ombor: {warehouse.name}" if warehouse else f"Ombor ID: {sale.location_id}"
        else:
            location_name = "Noma'lum"
        
        print(f"   Joylashuv: {location_name}")
        print(f"   Jami summa: ${float(sale.total_amount):,.2f}")
        print(f"   Foyda: ${float(sale.total_profit):,.2f}")
        print(f"   To'lov holati: {sale.payment_status}")
        print(f"   To'lov turi: {sale.payment_method}")
        
        # To'lov tafsilotlari
        if sale.cash_amount or sale.click_amount or sale.terminal_amount or sale.debt_amount:
            print(f"   To'lov tafsilotlari:")
            if sale.cash_amount > 0:
                print(f"      - Naqd: ${float(sale.cash_amount):,.2f}")
            if sale.click_amount > 0:
                print(f"      - Click: ${float(sale.click_amount):,.2f}")
            if sale.terminal_amount > 0:
                print(f"      - Terminal: ${float(sale.terminal_amount):,.2f}")
            if sale.debt_amount > 0:
                print(f"      - Qarz: ${float(sale.debt_amount):,.2f}")
        
        # Mahsulotlar
        print(f"   Mahsulotlar soni: {len(sale.items)} ta")
        for item in sale.items:
            product_name = item.product.name if item.product else "O'chirilgan mahsulot"
            
            # Har bir mahsulot joylashuvi
            if item.source_type == 'store' and item.source_id:
                item_store = Store.query.get(item.source_id)
                item_location = f"Do'kon: {item_store.name}" if item_store else f"Do'kon ID: {item.source_id}"
            elif item.source_type == 'warehouse' and item.source_id:
                item_warehouse = Warehouse.query.get(item.source_id)
                item_location = f"Ombor: {item_warehouse.name}" if item_warehouse else f"Ombor ID: {item.source_id}"
            else:
                item_location = "Noma'lum"
            
            print(f"      • {product_name}: {item.quantity} ta × ${float(item.unit_price):,.2f} = ${float(item.total_price):,.2f}")
            print(f"        Joylashuv: {item_location}")
            print(f"        Foyda: ${float(item.profit):,.2f}")
    
    # 4. Sale jadval strukturasi
    print(f"\n📄 SALE JADVALI STRUKTURASI (saqlanadigan ma'lumotlar):")
    print("-" * 80)
    print("Sale modelida quyidagi ma'lumotlar saqlanadi:")
    print("   • id - Savdo identifikatori")
    print("   • customer_id - Mijoz ID")
    print("   • store_id - Do'kon ID")
    print("   • location_id - Joylashuv ID (store yoki warehouse)")
    print("   • location_type - Joylashuv turi ('store' yoki 'warehouse')")
    print("   • seller_id - Sotuvchi ID")
    print("   • sale_date - Savdo sanasi va vaqti")
    print("   • total_amount - Jami summa")
    print("   • total_cost - Jami tan narx")
    print("   • total_profit - Jami foyda")
    print("   • payment_method - To'lov turi (cash, click, terminal, debt)")
    print("   • payment_status - To'lov holati (paid, pending)")
    print("   • cash_amount - Naqd pul summasi")
    print("   • click_amount - Click to'lov summasi")
    print("   • terminal_amount - Terminal to'lov summasi")
    print("   • debt_amount - Qarz summasi")
    print("   • notes - Qo'shimcha izohlar")
    print("   • currency_rate - Valyuta kursi")
    print("   • created_by - Kim tomonidan yaratilgan")
    print("   • created_at - Yaratilgan vaqti")
    
    print(f"\n📄 SALE_ITEMS JADVALI STRUKTURASI:")
    print("-" * 80)
    print("SaleItem modelida har bir mahsulot uchun quyidagi ma'lumotlar saqlanadi:")
    print("   • id - Item identifikatori")
    print("   • sale_id - Bog'langan savdo ID")
    print("   • product_id - Mahsulot ID")
    print("   • quantity - Miqdor")
    print("   • unit_price - Birlik narxi")
    print("   • total_price - Jami narx (quantity × unit_price)")
    print("   • cost_price - Tan narx (birlik)")
    print("   • profit - Foyda (total_price - (cost_price × quantity))")
    print("   • source_type - Mahsulot qayerdan ('store' yoki 'warehouse')")
    print("   • source_id - Source joylashuv ID")
    print("   • notes - Qo'shimcha izohlar")
    print("   • created_at - Yaratilgan vaqti")
    
    print("\n" + "=" * 80)
    print("✅ TEKSHIRUV YAKUNLANDI")
    print("=" * 80)

if __name__ == '__main__':
    check_sales()
