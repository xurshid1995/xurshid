# -*- coding: utf-8 -*-
import psycopg2
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        database=os.getenv('DB_NAME', 'sayt_db'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'postgres'),
        port=os.getenv('DB_PORT', '5432')
    )

def check_last_sale():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get last sale
    cur.execute('''
        SELECT id, customer_name, total_amount, payment_method, cash_received, change_amount, 
               discount, created_at, location, updated_at, updated_by_user_id, usd_equivalent, usd_rate
        FROM sales
        ORDER BY created_at DESC
        LIMIT 1
    ''')
    last_sale = cur.fetchone()
    
    if not last_sale:
        print("Savdolar topilmadi!")
        conn.close()
        return
    
    print('=' * 80)
    print('=== OXIRGI SAVDO MA\'LUMOTLARI ===')
    print('=' * 80)
    print(f'Sale ID: {last_sale[0]}')
    print(f'Mijoz: {last_sale[1]}')
    print(f'Jami summa (UZS): {last_sale[2]}')
    print(f'USD equivalent: {last_sale[11]}')
    print(f'USD kursi: {last_sale[12]}')
    print(f'To\'lov turi: {last_sale[3]}')
    print(f'Naqd olindi: {last_sale[4]}')
    print(f'Qaytim: {last_sale[5]}')
    print(f'Chegirma: {last_sale[6]}')
    print(f'Yaratildi: {last_sale[7]}')
    print(f'Joylashuv: {last_sale[8]}')
    print(f'Yangilandi: {last_sale[9]}')
    print(f'Yangilagan user ID: {last_sale[10]}')
    
    # Get sale items
    sale_id = last_sale[0]
    cur.execute('''
        SELECT si.id, si.product_id, p.name, si.product_name_snapshot, 
               si.quantity, si.price, si.total
        FROM sale_items si
        LEFT JOIN products p ON si.product_id = p.id
        WHERE si.sale_id = %s
        ORDER BY si.id
    ''', (sale_id,))
    items = cur.fetchall()
    
    print('\n' + '=' * 80)
    print(f'=== SAVDO ELEMENTLARI (Jami: {len(items)} ta mahsulot) ===')
    print('=' * 80)
    
    total_calculated = 0
    for i, item in enumerate(items, 1):
        print(f'\n{i}. Element ID: {item[0]}')
        print(f'   Product ID: {item[1]}')
        print(f'   Mahsulot nomi (DB): {item[2]}')
        print(f'   Mahsulot nomi (snapshot): {item[3]}')
        print(f'   Miqdor: {item[4]}')
        print(f'   Narx: {item[5]} UZS')
        print(f'   Jami: {item[6]} UZS')
        total_calculated += float(item[6]) if item[6] else 0
    
    print('\n' + '=' * 80)
    print(f'Hisoblangan jami: {total_calculated:,.2f} UZS')
    print(f'Sales jadvalidagi jami: {float(last_sale[2]):,.2f} UZS')
    
    if abs(total_calculated - float(last_sale[2])) > 0.01:
        print('\n⚠️ DIQQAT: Jami summada farq bor!')
    
    # Check for operations_history
    print('\n' + '=' * 80)
    print('=== OPERATIONS HISTORY ===')
    print('=' * 80)
    cur.execute('''
        SELECT id, operation_type, product_id, quantity, created_at, user_id
        FROM operations_history
        WHERE sale_id = %s
        ORDER BY created_at DESC
        LIMIT 10
    ''', (sale_id,))
    operations = cur.fetchall()
    
    if operations:
        for op in operations:
            print(f'\nOperation ID: {op[0]}')
            print(f'  Tur: {op[1]}')
            print(f'  Product ID: {op[2]}')
            print(f'  Miqdor: {op[3]}')
            print(f'  Vaqt: {op[4]}')
            print(f'  User ID: {op[5]}')
    else:
        print('Operations tarixi topilmadi')
    
    # Check stock for products in this sale
    print('\n' + '=' * 80)
    print('=== HOZIRGI STOCK HOLATI ===')
    print('=' * 80)
    for item in items:
        if item[1]:  # agar product_id bo'lsa
            cur.execute('''
                SELECT product_id, location, quantity
                FROM stock
                WHERE product_id = %s
                ORDER BY location
            ''', (item[1],))
            stock = cur.fetchall()
            
            print(f'\n{item[3] or item[2]} (ID: {item[1]}):')
            for s in stock:
                print(f'  {s[1]}: {s[2]} dona')
    
    conn.close()

if __name__ == '__main__':
    try:
        check_last_sale()
    except Exception as e:
        print(f"Xatolik: {e}")
        import traceback
        traceback.print_exc()
