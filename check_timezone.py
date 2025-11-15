#!/usr/bin/env python3
"""PostgreSQL va Python timezone tekshirish"""

import os
import sys
from datetime import datetime
from sqlalchemy import create_engine, text

# Database connection
DATABASE_URL = "postgresql://dokon_user:xurshid2025dokon@localhost/dokon_db"

try:
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        # PostgreSQL timezone
        result = conn.execute(text("SHOW timezone"))
        pg_timezone = result.fetchone()[0]
        print(f"✅ PostgreSQL timezone: {pg_timezone}")
        
        # PostgreSQL NOW()
        result = conn.execute(text("SELECT NOW()"))
        pg_now = result.fetchone()[0]
        print(f"✅ PostgreSQL NOW(): {pg_now}")
        
        # Python datetime.now()
        py_now = datetime.now()
        print(f"✅ Python datetime.now(): {py_now}")
        
        # Oxirgi 3 ta savdo sanasi
        result = conn.execute(text("SELECT id, sale_date FROM sales ORDER BY id DESC LIMIT 3"))
        sales = result.fetchall()
        print(f"\n✅ Oxirgi 3 ta savdo:")
        for sale in sales:
            print(f"   ID {sale[0]}: {sale[1]}")
            
        # Timezone farqi
        print(f"\n⏰ Python va PostgreSQL vaqt farqi: {py_now} vs {pg_now}")
        
except Exception as e:
    print(f"❌ Xatolik: {e}")
    sys.exit(1)
