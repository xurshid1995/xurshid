#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "========================================================================================================="
echo "                    PUT /api/sales/126 XATOLIGINI ANIQLASH (500 Error)                                   "
echo "========================================================================================================="

echo ""
echo "=== 1. GUNICORN ERROR LOG - 16:01:41 ATROFIDAGI XATOLIKLAR ==="
echo "Searching for Python errors around 16:01:41..."
tail -500 /var/log/gunicorn/error.log 2>/dev/null | grep -A20 -B5 "2026-02-08 16:01:4" | tail -50

echo ""
echo "=== 2. GUNICORN ACCESS LOG - PUT REQUEST DETALLARI ==="
grep "PUT /api/sales/126" /var/log/gunicorn/access.log 2>/dev/null | head -5

echo ""
echo "=== 3. FLASK APP LOG - UPDATE_SALE FUNKSIYASI ==="
grep -A10 "UPDATE Sale ID: 126" /var/log/gunicorn/error.log 2>/dev/null | head -30

echo ""
echo "=== 4. SYSTEMD JOURNAL - GUNICORN SERVICELARI ==="
echo "Checking systemd journal for gunicorn errors..."
journalctl -u gunicorn -u xurshid.service --since "2026-02-08 16:01:30" --until "2026-02-08 16:02:00" 2>/dev/null | tail -30

echo ""
echo "=== 5. PYTHON TRACEBACK QIDIRISH ==="
echo "Looking for Python tracebacks..."
grep -A15 "Traceback" /var/log/gunicorn/error.log 2>/dev/null | tail -50

echo ""
echo "========================================================================================================="
echo "                                    XATOLIK TAHLILI                                                      "
echo "========================================================================================================="
echo ""
echo "KUTILGAN XATOLIKLAR:"
echo "1. Database constraint violation (foreign key, unique)"
echo "2. Permission error (role-based access)"
echo "3. Data validation error"
echo "4. Stock insufficiency"
echo "5. NULL constraint violation"
echo ""
echo "========================================================================================================="
