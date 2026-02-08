#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "========================================================================================================="
echo "                    SALE 126 TIMELINE - MUAMMONI ANIQLASH                                               "
echo "========================================================================================================="

echo ""
echo "=== 1. SALE 126 YARATILISH VA YANGILANISH VAQTLARI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS.MS') as yaratildi,
    TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI:SS.MS') as yangilandi,
    EXTRACT(EPOCH FROM (updated_at - created_at)) as farq_sekund,
    created_by,
    notes,
    payment_status
FROM sales 
WHERE id = 126;
"

echo ""
echo "=== 2. OPERATIONS_HISTORY - SALE 126 UCHUN BARCHA OPERATSIYALAR ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    table_name,
    record_id,
    description,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS.MS') as vaqt,
    username,
    old_data,
    new_data
FROM operations_history 
WHERE table_name = 'sales' AND record_id = 126
ORDER BY created_at;
"

echo ""
echo "=== 3. SALE_ITEMS OPERATIONS - 16:01:00-16:02:00 ORALIG'I ==="
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
WHERE table_name = 'sale_items'
  AND created_at BETWEEN '2026-02-08 16:01:00' AND '2026-02-08 16:02:00'
ORDER BY created_at;
"

echo ""
echo "=== 4. TEYES Q8 MAHSULOTLARINI TEKSHIRISH ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    name,
    sell_price,
    barcode
FROM products 
WHERE id IN (92, 141, 144)
ORDER BY id;
"

echo ""
echo "=== 5. LENOVA VA TEYES STOCK OPERATSIYALARI (16:01:00-16:02:00) ==="
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
WHERE table_name IN ('store_stocks', 'warehouse_stocks')
  AND created_at BETWEEN '2026-02-08 16:01:00' AND '2026-02-08 16:02:00'
  AND (description LIKE '%97%' OR description LIKE '%144%' OR description LIKE '%92%' OR description LIKE '%141%')
ORDER BY created_at;
"

echo ""
echo "=== 6. GUNICORN/APP LOGLARINI TEKSHIRISH ==="
echo "Last 50 lines from gunicorn error log:"
tail -50 /var/log/gunicorn/error.log 2>/dev/null | grep -A5 -B5 "16:01" | tail -20

echo ""
echo "=== 7. NGINX ACCESS LOG - SALE 126 YARATISH REQUEST'LARI ==="
echo "Requests to /api/create-sale around 16:01:"
grep "16:01:" /var/log/nginx/access.log 2>/dev/null | grep "create-sale\|finalize-sale\|pending-sale" | tail -10

echo ""
echo "========================================================================================================="
echo "                                    XULOSALAR                                                            "
echo "========================================================================================================="
echo "1. Sale 126 yaratildi: 2026-02-08 16:01:35"
echo "2. Sale 126 yangilandi: 2026-02-08 16:01:45 (10 sekund keyin)"
echo "3. Agar bu vaqt oralig'ida sale_items qo'shilsa, muammo yo'q"
echo "4. Agar sale_items faqat yaratilish vaqtida qo'shilgan bo'lsa - muammo bor"
echo "========================================================================================================="
