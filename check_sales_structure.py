# -*- coding: utf-8 -*-
import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    database=os.getenv('DB_NAME', 'sayt_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', 'postgres'),
    port=os.getenv('DB_PORT', '5432')
)
cur = conn.cursor()

# Check sales table structure
print('=' * 80)
print('SALES JADVAL TUZILMASI')
print('=' * 80)
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'sales' 
    ORDER BY ordinal_position
""")
cols = cur.fetchall()
for col in cols:
    print(f'{col[0]}: {col[1]}')

# Get last sale
print('\n' + '=' * 80)
print('OXIRGI SAVDO')
print('=' * 80)
cur.execute('SELECT * FROM sales ORDER BY created_at DESC LIMIT 1')
last_sale = cur.fetchone()

if last_sale:
    # Get column names
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'sales' 
        ORDER BY ordinal_position
    """)
    col_names = [row[0] for row in cur.fetchall()]
    
    print(f'\nSale ID: {last_sale[0]}')
    for i, col_name in enumerate(col_names):
        print(f'{col_name}: {last_sale[i]}')
    
    # Get sale items
    sale_id = last_sale[0]
    print('\n' + '=' * 80)
    print('SAVDO ELEMENTLARI')
    print('=' * 80)
    cur.execute('''
        SELECT si.id, si.product_id, p.name, 
               si.quantity, si.unit_price, si.total_price, si.cost_price, si.profit
        FROM sale_items si
        LEFT JOIN products p ON si.product_id = p.id
        WHERE si.sale_id = %s
        ORDER BY si.id
    ''', (sale_id,))
    items = cur.fetchall()
    
    total_calc = 0
    for i, item in enumerate(items, 1):
        print(f'\n{i}. Element ID: {item[0]}')
        print(f'   Product ID: {item[1]}')
        print(f'   Mahsulot nomi: {item[2]}')
        print(f'   Miqdor: {item[3]}')
        print(f'   Birim narxi: {item[4]} UZS')
        print(f'   Jami narxi: {item[5]} UZS')
        print(f'   Sotib olinish narxi: {item[6]} UZS')
        print(f'   Foyda: {item[7]} UZS')
        if item[5]:
            total_calc += float(item[5])
    
    print(f'\n\nHISOBLANGAN JAMI: {total_calc:,.2f} UZS')
    
    # Check operations history for this sale
    print('\n' + '=' * 80)
    print('OPERATIONS HISTORY')
    print('=' * 80)
    cur.execute('''
        SELECT id, operation_type, product_id, quantity, created_at, user_id
        FROM operations_history
        WHERE reference_type = 'sale' AND reference_id = %s
        ORDER BY created_at DESC
        LIMIT 20
    ''', (sale_id,))
    ops = cur.fetchall()
    
    if ops:
        for op in ops:
            cur.execute('SELECT name FROM products WHERE id = %s', (op[2],))
            prod = cur.fetchone()
            prod_name = prod[0] if prod else 'NOMA''LUM'
            print(f'\nOperation ID: {op[0]}')
            print(f'  Tur: {op[1]}')
            print(f'  Mahsulot: {prod_name} (ID: {op[2]})')
            print(f'  Miqdor: {op[3]}')
            print(f'  Vaqt: {op[4]}')
            print(f'  User ID: {op[5]}')
    else:
        print('Operations topilmadi!')
else:
    print('Savdolar topilmadi!')

conn.close()
