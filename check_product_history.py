#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mahsulot miqdori o'zgarishini tekshirish
"""

import os
import sys
import urllib.parse
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Environment variables yuklash
load_dotenv()

# PostgreSQL ulanish parametrlari - localhost (server ichidan)
db_params = {
    'host': 'localhost',  # Server ichidan ulanish
    'port': '5432',
    'database': 'xurshid_db',
    'user': 'xurshid_user',
    'password': 'Xurshid2025!Strong'
}

# URL-safe qilish
safe_password = urllib.parse.quote_plus(db_params['password'])
safe_database = urllib.parse.quote_plus(db_params['database'])

# Clean URL yaratish
base_url = f"postgresql://{db_params['user']}:{safe_password}"
full_url = f"{base_url}@{db_params['host']}:{db_params['port']}"
DATABASE_URL = f"{full_url}/{safe_database}?client_encoding=utf8"

print(f"DB Host: {db_params['host']}")
print(f"DB Name: {db_params['database']}")
print(f"DB User: {db_params['user']}")

try:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    print("=" * 80)
    print("MA'LUMOTLAR BAZASIGA ULANISH - MUVAFFAQIYATLI")
    print("=" * 80)
    
    # Mahsulotni qidirish
    product_name = "Diamond Rul chixol zamish (Qora)"
    print(f"\n🔍 Mahsulot qidirilmoqda: {product_name}")
    
    # 1. Mahsulot ma'lumotlari
    product_query = text("""
        SELECT id, name, cost_price, sell_price, created_at
        FROM products
        WHERE name LIKE :name
    """)
    
    product = session.execute(product_query, {"name": f"%{product_name}%"}).fetchone()
    
    if not product:
        print(f"❌ Mahsulot topilmadi: {product_name}")
        sys.exit(1)
    
    product_id = product[0]
    print(f"\n✅ Mahsulot topildi:")
    print(f"   ID: {product_id}")
    print(f"   Nomi: {product[1]}")
    print(f"   Narxi: {product[2]} / {product[3]}")
    
    # 2. Hozirgi stock holati (do'konlar va omborlar)
    print(f"\n📦 HOZIRGI STOCK HOLATI:")
    print("-" * 80)
    
    # Store stocks
    store_stocks_query = text("""
        SELECT ss.id, s.name, ss.quantity, ss.last_updated
        FROM store_stocks ss
        JOIN stores s ON ss.store_id = s.id
        WHERE ss.product_id = :product_id
        ORDER BY ss.last_updated DESC
    """)
    
    store_stocks = session.execute(store_stocks_query, {"product_id": product_id}).fetchall()
    
    total_store_qty = 0
    for stock in store_stocks:
        print(f"   Do'kon: {stock[1]:<30} Miqdor: {stock[2]:>8} | Oxirgi o'zgarish: {stock[3]}")
        total_store_qty += float(stock[2])
    
    # Warehouse stocks
    warehouse_stocks_query = text("""
        SELECT ws.id, w.name, ws.quantity, ws.last_updated
        FROM warehouse_stocks ws
        JOIN warehouses w ON ws.warehouse_id = w.id
        WHERE ws.product_id = :product_id
        ORDER BY ws.last_updated DESC
    """)
    
    warehouse_stocks = session.execute(warehouse_stocks_query, {"product_id": product_id}).fetchall()
    
    total_warehouse_qty = 0
    for stock in warehouse_stocks:
        print(f"   Ombor: {stock[1]:<30} Miqdor: {stock[2]:>8} | Oxirgi o'zgarish: {stock[3]}")
        total_warehouse_qty += float(stock[2])
    
    total_qty = total_store_qty + total_warehouse_qty
    print(f"\n   JAMI MIQDOR: {total_qty}")
    
    # 3. Kechagi operatsiyalar (faqat kecha)
    yesterday = datetime.now().date() - timedelta(days=1)
    today = datetime.now().date()
    
    print(f"\n\n📊 KECHAGI OPERATSIYALAR ({yesterday}):")
    print("=" * 80)
    
    # Operations History
    ops_history_query = text("""
        SELECT 
            id,
            operation_type,
            description,
            old_data,
            new_data,
            username,
            location_name,
            created_at
        FROM operations_history
        WHERE record_id = :product_id
            AND DATE(created_at) = :yesterday
        ORDER BY created_at DESC
    """)
    
    ops = session.execute(ops_history_query, {
        "product_id": product_id,
        "yesterday": yesterday
    }).fetchall()
    
    if ops:
        print(f"\n   Operations History ({len(ops)} ta):")
        for op in ops:
            print(f"   - {op[7]} | {op[1]} | {op[5]} | {op[6]}")
            print(f"     {op[2]}")
            if op[3]:  # old_data
                print(f"     Eski: {op[3]}")
            if op[4]:  # new_data
                print(f"     Yangi: {op[4]}")
            print()
    else:
        print("   ✅ Kecha hech qanday operations_history yozilmagan")
    
    # Sales (kechadan bugungi kungacha)
    sales_query = text("""
        SELECT 
            s.id,
            s.sale_date,
            si.quantity,
            p.name as product_name,
            st.name as store_name,
            s.created_by,
            s.created_at
        FROM sales s
        JOIN sale_items si ON s.id = si.sale_id
        LEFT JOIN products p ON si.product_id = p.id
        LEFT JOIN stores st ON s.store_id = st.id
        WHERE si.product_id = :product_id
            AND DATE(s.sale_date) >= :yesterday
        ORDER BY s.sale_date DESC, s.created_at DESC
    """)
    
    sales = session.execute(sales_query, {
        "product_id": product_id,
        "yesterday": yesterday
    }).fetchall()
    
    if sales:
        print(f"\n   💰 SOTUVLAR ({len(sales)} ta):")
        total_sold = 0
        for sale in sales:
            print(f"   - Savdo #{sale[0]} | Sana: {sale[1]} | Miqdor: {sale[2]} | Do'kon: {sale[4]} | Sotuvchi: {sale[5]}")
            total_sold += float(sale[2])
        print(f"   JAMI SOTILDI: {total_sold}")
    else:
        print("   ✅ Kecha va bugun hech qanday savdo bo'lmagan")
    
    # Transfers (kechadan bugungi kungacha)
    transfers_query = text("""
        SELECT 
            t.id,
            t.created_at as transfer_date,
            t.quantity,
            t.from_location_type,
            t.from_location_id,
            t.to_location_type,
            t.to_location_id,
            t.user_name,
            t.created_at
        FROM transfers t
        WHERE t.product_id = :product_id
            AND DATE(t.created_at) >= :yesterday
        ORDER BY t.created_at DESC
    """)
    
    transfers = session.execute(transfers_query, {
        "product_id": product_id,
        "yesterday": yesterday
    }).fetchall()
    
    if transfers:
        print(f"\n   🔄 O'TKAZMALAR ({len(transfers)} ta):")
        for transfer in transfers:
            from_loc = f"{transfer[3]} (ID:{transfer[4]})"
            to_loc = f"{transfer[5]} (ID:{transfer[6]})"
            print(f"   - Transfer #{transfer[0]} | Sana: {transfer[1]} | Miqdor: {transfer[2]}")
            print(f"     {from_loc} -> {to_loc}")
            print(f"     Bajaruvchi: {transfer[7]} | Vaqt: {transfer[8]}")
    else:
        print("   ✅ Kecha va bugun hech qanday o'tkazma bo'lmagan")
    
    # Stock Check Sessions (inventarizatsiya)
    stock_check_query = text("""
        SELECT 
            scs.id,
            scs.session_name,
            scs.status,
            sci.actual_quantity,
            sci.difference,
            s.name as store_name,
            scs.created_at,
            scs.completed_at
        FROM stock_check_sessions scs
        JOIN stock_check_items sci ON scs.id = sci.session_id
        LEFT JOIN stores s ON scs.store_id = s.id
        WHERE sci.product_id = :product_id
            AND DATE(scs.created_at) >= :yesterday
        ORDER BY scs.created_at DESC
    """)
    
    stock_checks = session.execute(stock_check_query, {
        "product_id": product_id,
        "yesterday": yesterday
    }).fetchall()
    
    if stock_checks:
        print(f"\n   📋 INVENTARIZATSIYA ({len(stock_checks)} ta):")
        for check in stock_checks:
            print(f"   - Session #{check[0]} | {check[1]} | Status: {check[2]}")
            print(f"     Do'kon: {check[5]}")
            print(f"     Haqiqiy: {check[3]} | Farq: {check[4]}")
            print(f"     Boshlangan: {check[6]} | Tugagan: {check[7]}")
    else:
        print("   ✅ Kecha va bugun hech qanday inventarizatsiya bo'lmagan")
    
    # Barcha vaqtlardagi oxirgi 20 ta o'zgarish
    print(f"\n\n📜 OXIRGI 20 TA O'ZGARISH (barcha vaqt):")
    print("=" * 80)
    
    recent_ops_query = text("""
        SELECT 
            operation_type,
            description,
            username,
            location_name,
            created_at
        FROM operations_history
        WHERE (old_data::text LIKE '%product_id": ' || :product_id || '%'
            OR new_data::text LIKE '%product_id": ' || :product_id || '%'
            OR record_id = :product_id)
        ORDER BY created_at DESC
        LIMIT 20
    """)
    
    recent_ops = session.execute(recent_ops_query, {"product_id": product_id}).fetchall()
    
    if recent_ops:
        for op in recent_ops:
            print(f"   {op[4]} | {op[0]:<15} | {op[2]:<20} | {op[3]}")
            if op[1]:
                print(f"      {op[1][:100]}")
    else:
        print("   Hech qanday tarix topilmadi")
    
    print("\n" + "=" * 80)
    print("✅ TEKSHIRISH YAKUNLANDI")
    print("=" * 80)
    
except Exception as e:
    print(f"\n❌ XATOLIK YUZ BERDI: {str(e)}")
    import traceback
    traceback.print_exc()
finally:
    if 'session' in locals():
        session.close()
