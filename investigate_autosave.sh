#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "========================================================================================================="
echo "                    SALE 126 - AUTO-SAVE MUAMMOSINI ANIQLASH                                            "
echo "========================================================================================================="

echo ""
echo "=== 1. NGINX ACCESS LOG - BARCHA API REQUEST'LAR (16:01:00-16:02:00) ==="
grep "16:01:" /var/log/nginx/access.log 2>/dev/null | grep -E "pending-sales|create-sale|finalize-sale|api/sales" | head -20

echo ""
echo "=== 2. SALE 126 GA BOG'LIQ BARCHA OPERATSIYALAR ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    table_name,
    record_id,
    description,
    TO_CHAR(created_at, 'HH24:MI:SS.MS') as vaqt,
    username
FROM operations_history 
WHERE (table_name = 'sales' AND record_id = 126)
   OR (table_name = 'sale_items' AND created_at BETWEEN '2026-02-08 16:01:00' AND '2026-02-08 16:02:00')
ORDER BY created_at;
"

echo ""
echo "=== 3. SALE 126 YARATILISH VA YANGILANISH JARAYONI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    TO_CHAR(created_at, 'HH24:MI:SS.MS') as yaratildi,
    TO_CHAR(updated_at, 'HH24:MI:SS.MS') as yangilandi,
    EXTRACT(EPOCH FROM (updated_at - created_at))::numeric(10,3) as farq_sekund,
    created_by,
    payment_status,
    notes
FROM sales 
WHERE id = 126;
"

echo ""
echo "=== 4. GUNICORN ERROR LOG - PYTHON XATOLIKLARI (16:01 atrofida) ==="
echo "Looking for Python errors around 16:01..."
grep -A10 -B5 "16:01:" /var/log/gunicorn/error.log 2>/dev/null | tail -30

echo ""
echo "=== 5. GUNICORN ACCESS LOG - API CALLS ==="
echo "Looking for pending-sales and sale API calls..."
grep "pending-sales\|/api/sales" /var/log/gunicorn/access.log 2>/dev/null | grep "16:01:" | head -10

echo ""
echo "========================================================================================================="
echo "                                    TAHLIL                                                               "
echo "========================================================================================================="
echo ""
echo "MUHIM SAVOLLAR:"
echo "1. POST /api/pending-sales necha marta chaqirilgan?"
echo "2. PUT /api/sales/126 chaqirilganmi? (auto-save yangilash)"
echo "3. Har bir request'da nechta item bo'lgan?"
echo "4. Python loglarda xatolik bormi?"
echo ""
echo "KUTILGAN NATIJA:"
echo "- POST /api/pending-sales: 1 item (Lenova) bilan yaratilgan"
echo "- PUT /api/sales/126: Teyes Q8 qo'shilganda ishlamasligi kerak edi"
echo "- Sabab: Frontend'da cart yangilanmagan yoki auto-save ishlamagan"
echo "========================================================================================================="
