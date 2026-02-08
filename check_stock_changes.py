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
print("INVENTARIZATSIYA TEKSHIRUVI (Stock Check)")
print("=" * 80)
print(f"Mahsulot ID: {product_id}")
print(f"Sana: {yesterday} dan buyon")
print()

# Stock check items
cur.execute("""
    SELECT 
        sci.id,
        sci.system_quantity,
        sci.actual_quantity,
        sci.difference,
        sci.checked_at,
        scs.location_name,
        scs.status,
        scs.location_type,
        scs.location_id,
        scs.started_at,
        scs.updated_at
    FROM stock_check_items sci
    JOIN stock_check_sessions scs ON sci.session_id = scs.id
    WHERE sci.product_id = %s
        AND DATE(sci.checked_at) >= %s
    ORDER BY sci.checked_at DESC
""", (product_id, yesterday))

rows = cur.fetchall()

if rows:
    print(f"Topildi: {len(rows)} ta inventarizatsiya yozuvi\n")
    for r in rows:
        print(f"ID: {r[0]}")
        print(f"  Joylashuv: {r[5]} ({r[7]} ID: {r[8]})")
        print(f"  Status: {r[6]}")
        print(f"  System miqdor: {r[1]}")
        print(f"  Haqiqiy miqdor: {r[2]}")
        print(f"  ⚠️ FARQ: {r[3]}")
        print(f"  Tekshirilgan: {r[4]}")
        print(f"  Boshlangan: {r[9]}")
        print(f"  Yangilangan: {r[10]}")
        print("-" * 60)
else:
    print("Inventarizatsiya yozuvlari topilmadi")

# Jami store stock o'zgarishlari
print("\n" + "=" * 80)
print("STORE_STOCKS OXIRGI O'ZGARISHLAR")
print("=" * 80)

cur.execute("""
    SELECT 
        ss.id,
        s.name as store_name,
        ss.quantity,
        ss.last_updated
    FROM store_stocks ss
    JOIN stores s ON ss.store_id = s.id
    WHERE ss.product_id = %s
    ORDER BY ss.last_updated DESC
""", (product_id,))

store_stocks = cur.fetchall()
for ss in store_stocks:
    print(f"{ss[1]}: {ss[2]} | Oxirgi o'zgarish: {ss[3]}")

conn.close()
