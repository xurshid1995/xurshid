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

# Sales table structure
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'sales' 
    ORDER BY ordinal_position
""")
print("SALES TABLE STRUCTURE:")
print("=" * 40)
for row in cur.fetchall():
    print(f"{row[0]}: {row[1]}")

conn.close()
