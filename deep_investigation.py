#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import psycopg2
from datetime import datetime, timedelta

conn = psycopg2.connect(
    database='xurshid_db',
    user='xurshid_user', 
    password='Xurshid2025!Strong',
    host='localhost'
)
cur = conn.cursor()

product_id = 159
yesterday = (datetime.now() - timedelta(days=2)).date()

print("=" * 80)
print(f"MUAMMONI TOPISH: Mahsulot #{product_id}")
print(f"Sana: {yesterday} dan buyon")
print("=" * 80)

# 1. Operations history umumiy
print("\n1️⃣ BARCHA OPERATIONS_HISTORY (oxirgi 10 ta):")
print("-" * 80)
cur.execute("""
    SELECT 
        operation_type,
        description,
        username,
        location_name,
        created_at,
        old_data,
        new_data
    FROM operations_history
    WHERE (old_data::text LIKE %s OR new_data::text LIKE %s)
        OR (description LIKE %s)
    ORDER BY created_at DESC
    LIMIT 10
""", (f'%"product_id": {product_id}%', f'%"product_id": {product_id}%', f'%{product_id}%'))

ops = cur.fetchall()
if ops:
    for op in ops:
        print(f"📌 {op[4]} | {op[0]} | {op[2]} | {op[3]}")
        print(f"   {op[1]}")
        if op[5]:  # old_data
            print(f"   Old: {op[5]}")
        if op[6]:  # new_data
            print(f"   New: {op[6]}")
        print()
else:
    print("   Yo'q\n")

# 2. Barcha jadvallardan product_id = 159 ni qidirish (oxirgi 48 soat)
print("\n2️⃣ TO'G'RIDAN-TO'G'RI STORE_STOCKS JADVALNI TEKSHIRISH:")
print("-" * 80)

# Store stocks changes - oxirgi 2 kun ichida
cur.execute("""
    SELECT 
        ss.id,
        s.name as store_name,
        ss.quantity,
        ss.last_updated
    FROM store_stocks ss
    JOIN stores s ON ss.store_id = s.id
    WHERE ss.product_id = %s
        AND ss.last_updated >= %s
    ORDER BY ss.last_updated DESC
""", (product_id, yesterday))

recent_changes = cur.fetchall()
if recent_changes:
    print(f"Topildi: {len(recent_changes)} ta o'zgarish")
    for rc in recent_changes:
        print(f"  {rc[3]} | {rc[1]}: {rc[2]}")
else:
    print("  Oxirgi 2 kun ichida hech qanday o'zgarish yo'q")

# 3. PostgreSQL audit log (agar mavjud bo'lsa)
print("\n3️⃣ QANDAY AMALIYOT BAJARILGAN (store_stocks ga):")
print("-" * 80)
print("  Tekshirilayotgan vaqt: 2026-02-06 17:21:42")
print("  Miqdor: 370 -> 369")
print()

# Oxirgi sale_items - bu mahsulot sotilganmi?
cur.execute("""
    SELECT 
        si.id,
        si.sale_id,
        si.quantity,
        s.sale_date,
        s.created_by,
        st.name as store_name
    FROM sale_items si
    JOIN sales s ON si.sale_id = s.id
    LEFT JOIN stores st ON s.store_id = st.id
    WHERE si.product_id = %s
        AND s.sale_date >= %s
    ORDER BY s.sale_date DESC
    LIMIT 10
""", (product_id, yesterday))

recent_sales = cur.fetchall()
if recent_sales:
    print(f"\n4️⃣ OXIRGI SOTUVLAR ({len(recent_sales)} ta):")
    print("-" * 80)
    for rs in recent_sales:
        print(f"  Sale #{rs[1]} | {rs[3]} | Miqdor: {rs[2]} | {rs[5]} | Sotuvchi: {rs[4]}")
else:
    print("\n4️⃣ Oxirgi sotuvlar topilmadi")

# ProductBilan bog'liq barcha ma'lumotlar
print("\n\n5️⃣ MAHSULOT MA'LUMOTLARI:")
print("-" * 80)
cur.execute("""
    SELECT 
        id, name, cost_price, sell_price, created_at, is_checked
    FROM products
    WHERE id = %s
""", (product_id,))

product = cur.fetchone()
if product:
    print(f"  ID: {product[0]}")
    print(f"  Nomi: {product[1]}")
    print(f"  Narx: {product[2]} /  {product[3]}")
    print(f"  Qo'shilgan: {product[4]}")
    print(f"  Tekshirilgan: {product[5]}")

conn.close()

print("\n" + "=" * 80)
print("XULOSA:")
print("=" * 80)
print("Agar yuqorida hech narsa topilmasa, bu shuni anglatadiki:")
print("1. Miqdor to'g'ridan-to'g'ri SQL orqali o'zgartirilgan")
print("2. Yoki tizimda bug bor")
print("3. Yoki savdo/o'tkazma qayd etilmagan")
print("=" * 80)
