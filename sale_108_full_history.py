#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import psycopg2
import json

conn = psycopg2.connect(
    database='xurshid_db',
    user='xurshid_user', 
    password='Xurshid2025!Strong',
    host='localhost'
)
cur = conn.cursor()

print("=" * 80)
print("SALE #108 - TO'LIQ TARIX")
print("=" * 80)

# 1. Operations history - BARCHA operatsiyalar
cur.execute("""
    SELECT 
        id,
        operation_type,
        description,
        username,
        old_data,
        new_data,
        location_name,
        amount,
        created_at
    FROM operations_history
    WHERE (record_id = 108 AND table_name = 'sales')
        OR (old_data::text LIKE '%"sale_id": 108%')
        OR (new_data::text LIKE '%"sale_id": 108%')
        OR (description LIKE '%Sale #108%' OR description LIKE '%Sale ID 108%')
    ORDER BY created_at ASC
""")

ops = cur.fetchall()

if ops:
    print(f"\nTopildi: {len(ops)} ta operatsiya\n")
    for i, op in enumerate(ops, 1):
        print(f"{i}. {op[8]} | {op[1].upper()}")
        print(f"   User: {op[3]} | Location: {op[6]}")
        print(f"   Description: {op[2]}")
        
        if op[4]:  # old_data
            try:
                old = json.loads(op[4]) if isinstance(op[4], str) else op[4]
                print(f"   OLD DATA:")
                if isinstance(old, dict):
                    for key, val in old.items():
                        if key in ['product_id', 'product_name', 'quantity', 'sale_id', 'items']:
                            print(f"     {key}: {val}")
                else:
                    print(f"     {old}")
            except:
                print(f"   OLD DATA: {str(op[4])[:100]}")
        
        if op[5]:  # new_data
            try:
                new = json.loads(op[5]) if isinstance(op[5], str) else op[5]
                print(f"   NEW DATA:")
                if isinstance(new, dict):
                    for key, val in new.items():
                        if key in ['product_id', 'product_name', 'quantity', 'sale_id', 'items']:
                            print(f"     {key}: {val}")
                else:
                    print(f"     {new}")
            except:
                print(f"   NEW DATA: {str(op[5])[:100]}")
        
        print("-" * 80)
else:
    print("❌ Operations history'da hech narsa topilmadi")

# 2. Mahsulot #159 bilan bog'liq BARCHA operatsiyalar (o'sha kuni)
print("\n" + "=" * 80)
print("MAHSULOT #159 - 2026-02-06 KUNI BARCHA OPERATSIYALAR:")
print("=" * 80)

cur.execute("""
    SELECT 
        operation_type,
        description,
        username,
        location_name,
        amount,
        created_at,
        old_data,
        new_data
    FROM operations_history
    WHERE DATE(created_at) = '2026-02-06'
        AND (
            (old_data::text LIKE '%"product_id": 159%')
            OR (new_data::text LIKE '%"product_id": 159%')
            OR (description LIKE '%Diamond Rul chixol zamish%')
        )
    ORDER BY created_at ASC
""")

product_ops = cur.fetchall()

if product_ops:
    print(f"\nTopildi: {len(product_ops)} ta operatsiya\n")
    for i, op in enumerate(product_ops, 1):
        print(f"{i}. {op[5]} | {op[0]}")
        print(f"   {op[1]}")
        print(f"   User: {op[2]} | Location: {op[3]}")
        
        # Quantity ni topish
        try:
            if op[6]:  # old_data
                old = json.loads(op[6]) if isinstance(op[6], str) else op[6]
                if isinstance(old, dict) and 'quantity' in old:
                    print(f"   Old Quantity: {old['quantity']}")
            
            if op[7]:  # new_data
                new = json.loads(op[7]) if isinstance(op[7], str) else op[7]
                if isinstance(new, dict) and 'quantity' in new:
                    print(f"   New Quantity: {new['quantity']}")
        except:
            pass
        
        print()
else:
    print("Topilmadi")

conn.close()

print("=" * 80)
print("XULOSA:")
print("=" * 80)
print("Agar Sale #108 edit qilingan bo'lsa, operations_history'da")
print("'edit' yoki 'update' yozuvi bo'lishi kerak.")
print("=" * 80)
