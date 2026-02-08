#!/usr/bin/env python3
import psycopg2

conn = psycopg2.connect(
    host="164.92.177.172",
    database="xurshid_db", 
    user="xurshid_user",
    password="xurshid2024"
)

cur = conn.cursor()

# Bugungi umumiy statistika
cur.execute("""
    SELECT 
        COUNT(*) as savdolar,
        SUM(total_amount) as total,
        SUM(cash_usd) as naqd,
        SUM(click_usd) as click,
        SUM(terminal_usd) as terminal,
        SUM(debt_usd) as qarz
    FROM sales 
    WHERE DATE(sale_date) = CURRENT_DATE;
""")

row = cur.fetchone()
print("BUGUNGI UMUMIY STATISTIKA:")
print(f"Savdolar soni: {row[0]}")
print(f"Total amount:  ${row[1]:.2f}")
print(f"Naqd:          ${row[2]:.2f}")
print(f"Click:         ${row[3]:.2f}")
print(f"Terminal:      ${row[4]:.2f}")
print(f"Qarz:          ${row[5]:.2f}")
print(f"To'lovlar jami: ${row[2] + row[3] + row[4] + row[5]:.2f}")
print()

# Dashboard'dagi query - faqat to'lov ma'lumotlari bor savdolar
cur.execute("""
    SELECT 
        COUNT(*) as savdolar,
        SUM(total_amount) as total,
        SUM(cash_usd) as naqd,
        SUM(click_usd) as click,
        SUM(terminal_usd) as terminal,
        SUM(debt_usd) as qarz
    FROM sales 
    WHERE DATE(sale_date) = CURRENT_DATE
    AND (cash_usd > 0 OR click_usd > 0 OR terminal_usd > 0 OR debt_usd > 0);
""")

row2 = cur.fetchone()
print("DASHBOARD QUERY NATIJASI (WHERE to'lov > 0):")
print(f"Savdolar soni: {row2[0]}")
print(f"Total amount:  ${row2[1]:.2f}")
print(f"Naqd:          ${row2[2]:.2f}")
print(f"Click:         ${row2[3]:.2f}")
print(f"Terminal:      ${row2[4]:.2f}")
print(f"Qarz:          ${row2[5]:.2f}")
print()

# Xatoni ko'rsatish
print("=" * 50)
print("FARQ:")
print(f"Total amount farqi: ${row[1] - row2[1]:.2f}")
print(f"Naqd farqi:         ${row[2] - row2[2]:.2f}")

cur.close()
conn.close()
