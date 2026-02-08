#!/usr/bin/env python3
import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

conn = psycopg2.connect(
    database=os.getenv('DB_NAME', 'xurshid_db'),
    user=os.getenv('DB_USER', 'xurshid_user'),
    password=os.getenv('DB_PASSWORD'),
    host=os.getenv('DB_HOST', 'localhost')
)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'transfers' ORDER BY ordinal_position")
print('\n'.join([row[0] for row in cur.fetchall()]))
conn.close()
