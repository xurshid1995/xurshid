#!/bin/bash

echo "=== SAYT LOG MONITORING ==="
echo "Vaqt: $(date)"
echo ""

# Error logni tekshirish
echo "--- XATOLAR (oxirgi 20 qator) ---"
if [ -f logs/error.log ]; then
    tail -20 logs/error.log | grep -i "error\|critical\|exception" || echo "Xatolar topilmadi ✅"
else
    echo "error.log fayli topilmadi"
fi

echo ""
echo "--- ACCESS LOG (oxirgi 10 request) ---"
if [ -f logs/access.log ]; then
    tail -10 logs/access.log
else
    echo "access.log fayli topilmadi"
fi

echo ""
echo "--- GUNICORN STATUS ---"
systemctl status sayt2025.service --no-pager | head -15

echo ""
echo "--- DISK SPACE ---"
df -h / | tail -1

echo ""
echo "--- MEMORY USAGE ---"
free -h | grep -E "Mem|Swap"

echo ""
echo "--- CPU LOAD ---"
uptime

echo ""
echo "--- ACTIVE CONNECTIONS (PostgreSQL) ---"
sudo -u postgres psql -c "SELECT count(*) as active_connections FROM pg_stat_activity WHERE state = 'active';" 2>/dev/null || echo "PostgreSQL connection xatosi"

echo ""
echo "=== MONITORING TUGADI ==="
