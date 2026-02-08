#!/usr/bin/env python3
"""Serverdagi savdolarni tekshirish"""
import psycopg2
from datetime import datetime

# Server connection
conn = psycopg2.connect(
    host="164.92.177.172",
    database="xurshid_db",
    user="xurshid_user",
    password="xurshid2024"
)

cur = conn.cursor()

print("=" * 80)
print("BUGUNGI SAVDOLAR TEKSHIRUVI")
print("=" * 80)

# Bugungi savdolarni olish
query = """
SELECT 
    id,
    TO_CHAR(sale_date, 'HH24:MI') as time,
    total_amount,
    cash_usd,
    click_usd,
    terminal_usd,
    debt_usd,
    ROUND(cash_usd + click_usd + terminal_usd + debt_usd, 2) as payment_sum
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE 
ORDER BY sale_date DESC 
LIMIT 20;
"""

cur.execute(query)
rows = cur.fetchall()

print(f"\nJami {len(rows)} ta savdo topildi\n")
print(f"{'ID':<7} {'Vaqt':<7} {'Total':>10} {'Naqd':>10} {'Click':>10} {'Terminal':>10} {'Qarz':>10} {'To\'lov':>10} Status")
print("-" * 100)

xato_soni = 0
for row in rows:
    id, time, total, cash, click, terminal, debt, payment_sum = row
    
    # To'g'ri yoki xato
    diff = round(abs(float(total) - float(payment_sum)), 2)
    status = "✅ OK" if diff < 0.01 else f"❌ XATO ({diff})"
    
    if diff >= 0.01:
        xato_soni += 1
    
    print(f"{id:<7} {time:<7} ${float(total):>9.2f} ${float(cash):>9.2f} ${float(click):>9.2f} ${float(terminal):>9.2f} ${float(debt):>9.2f} ${float(payment_sum):>9.2f} {status}")

# Umumiy statistika
cur.execute("""
SELECT 
    COUNT(*) as total_sales,
    ROUND(SUM(total_amount), 2) as total_sum,
    ROUND(SUM(cash_usd), 2) as cash_sum,
    ROUND(SUM(click_usd), 2) as click_sum,
    ROUND(SUM(terminal_usd), 2) as terminal_sum,
    ROUND(SUM(debt_usd), 2) as debt_sum,
    ROUND(SUM(cash_usd + click_usd + terminal_usd + debt_usd), 2) as payment_total
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE;
""")

stats = cur.fetchone()

print("\n" + "=" * 80)
print("UMUMIY STATISTIKA")
print("=" * 80)
print(f"Jami savdolar: {stats[0]}")
print(f"Total amount:  ${float(stats[1]):,.2f}")
print(f"Naqd:          ${float(stats[2]):,.2f}")
print(f"Click:         ${float(stats[3]):,.2f}")
print(f"Terminal:      ${float(stats[4]):,.2f}")
print(f"Qarz:          ${float(stats[5]):,.2f}")
print(f"To'lovlar jami: ${float(stats[6]):,.2f}")
print()

diff_total = abs(float(stats[1]) - float(stats[6]))
if diff_total < 0.01:
    print("✅ Barchasi to'g'ri!")
else:
    print(f"❌ XATO ANIQLANDI! Farq: ${diff_total:,.2f}")
    print(f"   Xato savdolar: {xato_soni} ta")

cur.close()
conn.close()
