#!/bin/bash

echo "=============================================================="
echo "ANIQ SABAB TOPISH - 2026-02-06 17:21:42 atrofida"
echo "=============================================================="

# 1. NGINX ACCESS LOGS - HTTP requestlarni topish
echo
echo "1️⃣ NGINX ACCESS LOGS (17:20-17:25 oralig'i):"
echo "--------------------------------------------------------------"

if [ -f "/var/log/nginx/access.log" ]; then
    echo "Barcha requestlar 17:20-17:25 oralig'ida:"
    grep "06/Feb/2026:17:2[0-5]:" /var/log/nginx/access.log | tail -30
    echo
    echo "Maxsus stock/product/sale bilan bog'liq requestlar:"
    grep "06/Feb/2026:17:2[0-5]:" /var/log/nginx/access.log | grep -E "(stock|product|sale|edit)" | tail -20
else
    echo "❌ /var/log/nginx/access.log topilmadi"
fi

# 2. Agar nginx log rotation qilingan bo'lsa
if [ -f "/var/log/nginx/access.log.1" ]; then
    echo
    echo "Arxivlangan nginx log:"
    grep "06/Feb/2026:17:2[0-5]:" /var/log/nginx/access.log.1 | tail -30
fi

# 3. SYSTEMD JOURNAL - gunicorn/flask loglar
echo
echo "2️⃣ GUNICORN/FLASK JOURNAL LOGS:"
echo "--------------------------------------------------------------"
echo "2026-02-06 17:20-17:25 oralig'idagi barcha loglar:"
journalctl --since "2026-02-06 17:20:00" --until "2026-02-06 17:25:00" | grep -E "(POST|PUT|stock|product|159|369|370)" | tail -30

# 4. PostgreSQL connection activity
echo
echo "3️⃣ PostgreSQL CONNECTION va ACTIVITY:"
echo "--------------------------------------------------------------"

# Active connectionlar
sudo -u postgres psql -d xurshid_db -c "
    SELECT 
        datname, 
        usename,
        application_name,
        client_addr,
        backend_start,
        state_change,
        state
    FROM pg_stat_activity 
    WHERE datname = 'xurshid_db'
    ORDER BY state_change DESC
    LIMIT 10;
"

# 5. PostgreSQL pg_stat_statements (agar yoqilgan bo'lsa)
echo
echo "4️⃣ OXIRGI BAJARILGAN QUERYLAR (pg_stat_statements):"
echo "--------------------------------------------------------------"
sudo -u postgres psql -d xurshid_db -c "
    SELECT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
    ) as extension_installed;
"

# 6. Last modified files
echo
echo "5️⃣ 2026-02-06 KUNI O'ZGARTIRILGAN FAYLLAR:"
echo "--------------------------------------------------------------"
find /var/www/xurshid -type f -newermt "2026-02-06 17:00:00" ! -newermt "2026-02-06 18:00:00" -ls 2>/dev/null | head -20

# 7. Who logged in
echo
echo "6️⃣ SSH LOGIN HISTORY (2026-02-06):"
echo "--------------------------------------------------------------"
last -F | grep "Feb  6" | head -20

echo
echo "7️⃣ AUTH LOG - Root/User login attempts:"
echo "--------------------------------------------------------------"
grep "Feb  6 17:2" /var/log/auth.log 2>/dev/null | tail -20

echo
echo "=============================================================="
echo "XULOSA:"
echo "--------------------------------------------------------------"
echo "✅ Agar NGINX logda request topilsa → UI orqali o'zgartirilgan"
echo "✅ Agar SSH login + psql activity → Manual SQL edit"
echo "✅ Agar hech narsa topilmasa → Transaction BUG yoki system issue"
echo "=============================================================="
