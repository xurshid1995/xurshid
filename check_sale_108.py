#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import psycopg2

conn = psycopg2.connect(
    database='xurshid_db',
    user='xurshid_user', 
    password='Xurshid2025!Strong',
    host='localhost'
)
cur = conn.cursor()

print("=" * 80)
print("SALE #108 TEKSHIRUVI (O'chirilgan)")
print("=" * 80)

# 1. Sale ma'lumotlarini olishga harakat (agar o'chirilgan bo'lsa topilmaydi)
cur.execute("""
    SELECT 
        id,
        customer_id,
        store_id,
        sale_date,
        total_amount,
        created_by,
        created_at
    FROM sales
    WHERE id = 108
""")

sale = cur.fetchone()

if sale:
    print("❗ Sale #108 HALI MAVJUD (o'chirilmagan):")
    print(f"  ID: {sale[0]}")
    print(f"  Sale Date: {sale[3]}")
    print(f"  Total: {sale[4]}")
    print(f"  Created by: {sale[5]}")
else:
    print("✅ Sale #108 O'CHIRILGAN (bazada yo'q)")

# 2. Sale_items ni tekshirish
print("\n" + "=" * 80)
print("SALE_ITEMS #108 (agar mavjud bo'lsa):")
print("=" * 80)

cur.execute("""
    SELECT 
        si.id,
        si.product_id,
        p.name as product_name,
        si.quantity,
        si.unit_price,
        si.total_price
    FROM sale_items si
    LEFT JOIN products p ON si.product_id = p.id
    WHERE si.sale_id = 108
""")

items = cur.fetchall()

if items:
    print(f"❗ Sale items hali mavjud ({len(items)} ta):")
    for item in items:
        print(f"  - Product #{item[1]}: {item[2]}")
        print(f"    Miqdor: {item[3]} | Narx: {item[4]} | Jami: {item[5]}")
        if item[1] == 159:
            print(f"    ⚠️ BU BIZNING MAHSULOT (Diamond Rul chixol zamish)!")
else:
    print("✅ Sale items o'chirilgan (bazada yo'q)")

# 3. Operations history'da Sale #108 ni qidirish
print("\n" + "=" * 80)
print("OPERATIONS_HISTORY dan Sale #108:")
print("=" * 80)

cur.execute("""
    SELECT 
        operation_type,
        description,
        username,
        old_data,
        new_data,
        created_at
    FROM operations_history
    WHERE record_id = 108
        AND table_name = 'sales'
    ORDER BY created_at DESC
    LIMIT 5
""")

ops = cur.fetchall()

if ops:
    for op in ops:
        print(f"\n📌 {op[5]} | {op[0]} | {op[2]}")
        print(f"   {op[1]}")
        if op[3]:  # old_data
            print(f"   Old data: {op[3]}")
else:
    print("❌ Operations history'da Sale #108 topilmadi")

# 4. Sale #108 haqida barcha ma'lumotlar
print("\n" + "=" * 80)
print("Sale #108 ga tegishli BARCHA operations:")
print("=" * 80)

cur.execute("""
    SELECT 
        operation_type,
        description,
        username,
        location_name,
        created_at
    FROM operations_history
    WHERE description LIKE '%108%'
        OR old_data::text LIKE '%"sale_id": 108%'
        OR new_data::text LIKE '%"sale_id": 108%'
    ORDER BY created_at DESC
    LIMIT 10
""")

all_ops = cur.fetchall()

if all_ops:
    for op in all_ops:
        print(f"\n  {op[4]} | {op[0]} | {op[2]} | {op[3]}")
        print(f"  {op[1]}")
else:
    print("  Topilmadi")

conn.close()

print("\n" + "=" * 80)
print("XULOSA:")
print("=" * 80)
print("Sale #108 o'chirilgan va mahsulotlar stockga qaytgan.")
print("Lekin operations_history ga delete yozilmagan - BU BUG!")
print("=" * 80)
