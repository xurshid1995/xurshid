#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serverdan oxirgi savdoni tekshirish scripti
Ishlatish: python3 check_server_last_sale.py
"""

import psycopg2
from datetime import date
import sys

# Server database connection
try:
    conn = psycopg2.connect(
        host='localhost',
        database='xurshid_db',
        user='xurshid_user',
        password='Xurshid2025!Strong',
        port='5432'
    )
    cur = conn.cursor()
    print("✓ Database ga ulandi")
except Exception as e:
    print(f"✗ Database ga ulanishda xatolik: {e}")
    sys.exit(1)

# Get today's sales count
today = date.today()
cur.execute('SELECT COUNT(*) FROM sales WHERE DATE(created_at) = %s', (today,))
today_count = cur.fetchone()[0]
print(f"\n📊 BUGUN ({today}) SAVDOLAR SONI: {today_count}")

# Get last sale
print('\n' + '=' * 80)
print('📝 OXIRGI SAVDO MA\'LUMOTLARI')
print('=' * 80)

cur.execute('''
    SELECT id, customer_id, store_id, sale_date, total_amount, total_cost, 
           total_profit, payment_method, payment_status, notes, created_by, 
           created_at, currency_rate, seller_id
    FROM sales
    ORDER BY created_at DESC
    LIMIT 1
''')
last_sale = cur.fetchone()

if not last_sale:
    print("❌ Savdolar topilmadi!")
    conn.close()
    sys.exit(0)

print(f'\n🆔 Sale ID: {last_sale[0]}')
print(f'👤 Customer ID: {last_sale[1]}')
print(f'🏪 Store ID: {last_sale[2]}')
print(f'📅 Sana: {last_sale[3]}')
print(f'💵 Jami summa: {last_sale[4]} USD')
print(f'💰 Xarajat: {last_sale[5]} USD')
print(f'📈 Foyda: {last_sale[6]} USD')
print(f'💳 To\'lov turi: {last_sale[7]}')
print(f'✅ To\'lov holati: {last_sale[8]}')
print(f'📝 Izoh: {last_sale[9]}')
print(f'👨‍💻 Yaratuvchi: {last_sale[10]}')
print(f'🕐 Yaratilgan: {last_sale[11]}')
print(f'💱 Valyuta kursi: {last_sale[12]}')
print(f'🧑‍💼 Sotuvchi ID: {last_sale[13]}')

# Calculate UZS equivalent
if last_sale[4] and last_sale[12]:
    uzs_amount = float(last_sale[4]) * float(last_sale[12])
    print(f'\n💵 UZS da: {uzs_amount:,.2f} UZS')

# Get sale items
sale_id = last_sale[0]
print('\n' + '=' * 80)
print('📦 SAVDO ELEMENTLARI')
print('=' * 80)

cur.execute('''
    SELECT si.id, si.product_id, p.name, si.quantity, 
           si.unit_price, si.total_price, si.cost_price, si.profit,
           si.source_type, si.notes
    FROM sale_items si
    LEFT JOIN products p ON si.product_id = p.id
    WHERE si.sale_id = %s
    ORDER BY si.id
''', (sale_id,))
items = cur.fetchall()

if not items:
    print("❌ DIQQAT: Bu savdoda hech qanday mahsulot topilmadi!")
else:
    total_calc = 0
    for i, item in enumerate(items, 1):
        print(f'\n{i}. 🏷️  Element ID: {item[0]}')
        print(f'   📦 Product ID: {item[1]}')
        print(f'   📝 Mahsulot: {item[2] or "NOMA\'LUM"}')
        print(f'   #️⃣  Miqdor: {item[3]}')
        print(f'   💵 Birim narxi: ${item[4]}')
        print(f'   💰 Jami: ${item[5]}')
        print(f'   📊 Sotib olinish: ${item[6]}')
        print(f'   📈 Foyda: ${item[7]}')
        if item[8]:
            print(f'   🔗 Manba: {item[8]}')
        if item[9]:
            print(f'   📝 Izoh: {item[9]}')
        if item[5]:
            total_calc += float(item[5])

    print(f'\n💵 Hisoblangan jami: ${total_calc:.2f}')
    print(f'💵 Sales jadvalidagi jami: ${float(last_sale[4]):.2f}')
    
    if abs(total_calc - float(last_sale[4])) > 0.01:
        print('\n⚠️  DIQQAT: Summada farq bor!')
        print(f'   Farq: ${abs(total_calc - float(last_sale[4])):.2f}')

# Check customer info
if last_sale[1]:
    print('\n' + '=' * 80)
    print('👤 MIJOZ MA\'LUMOTLARI')
    print('=' * 80)
    cur.execute('SELECT id, name, phone, notes FROM customers WHERE id = %s', (last_sale[1],))
    customer = cur.fetchone()
    if customer:
        print(f'ID: {customer[0]}')
        print(f'Ism: {customer[1]}')
        print(f'Telefon: {customer[2]}')
        if customer[3]:
            print(f'Izoh: {customer[3]}')

# Check stock for products
print('\n' + '=' * 80)
print('📊 MAHSULOTLAR STOCK HOLATI')
print('=' * 80)

for item in items:
    if item[1]:  # if product_id exists
        cur.execute('''
            SELECT location, quantity 
            FROM stock 
            WHERE product_id = %s
            ORDER BY location
        ''', (item[1],))
        stocks = cur.fetchall()
        
        print(f'\n📦 {item[2] or "NOMA\'LUM"} (ID: {item[1]}):')
        if stocks:
            for st in stocks:
                print(f'   {st[0]}: {st[1]} dona')
        else:
            print('   ⚠️  Stock ma\'lumotlari yo\'q')

conn.close()
print('\n✓ Tekshirish tugadi')
