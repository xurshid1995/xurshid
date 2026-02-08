import psycopg2

try:
    conn = psycopg2.connect(
        host="164.92.177.172",
        database="xurshid_db", 
        user="xurshid_user",
        password="xurshid2024",
        connect_timeout=10
    )
    
    cur = conn.cursor()
    
    # Bugungi statistika
    cur.execute("""
        SELECT 
            COUNT(*) as savdolar,
            COALESCE(SUM(total_amount), 0) as total,
            COALESCE(SUM(cash_usd), 0) as naqd,
            COALESCE(SUM(click_usd), 0) as click,
            COALESCE(SUM(terminal_usd), 0) as terminal,
            COALESCE(SUM(debt_usd), 0) as qarz
        FROM sales 
        WHERE DATE(sale_date) = CURRENT_DATE;
    """)
    
    row = cur.fetchone()
    
    print("=" * 60)
    print("SERVERDAGI HAQIQIY MA'LUMOTLAR (bugun):")
    print("=" * 60)
    print(f"Savdolar soni:  {row[0]}")
    print(f"Total amount:   ${float(row[1]):.2f}")
    print(f"Naqd (cash):    ${float(row[2]):.2f}")
    print(f"Click:          ${float(row[3]):.2f}")
    print(f"Terminal:       ${float(row[4]):.2f}")
    print(f"Qarz (debt):    ${float(row[5]):.2f}")
    
    tolov_jami = float(row[2]) + float(row[3]) + float(row[4]) + float(row[5])
    print(f"To'lovlar jami: ${tolov_jami:.2f}")
    
    farq = float(row[1]) - tolov_jami
    print()
    if abs(farq) < 0.01:
        print("✅ Ma'lumotlar to'g'ri - total = to'lovlar jami")
    else:
        print(f"⚠️ FARQ bor: ${farq:.2f}")
    
    cur.close()
    conn.close()
    
    print("\n✅ Server bilan aloqa muvaffaqiyatli!")
    
except Exception as e:
    print(f"❌ Xatolik: {e}")
