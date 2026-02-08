#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "================================"
echo "SALE 126 OPERATIONS HISTORY"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    table_name,
    record_id,
    username,
    description,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS.MS') as vaqt
FROM operations_history 
WHERE table_name = 'sales' AND record_id = 126
ORDER BY created_at;
"

echo ""
echo "================================"
echo "SALE_ITEMS OPERATIONS (16:00-16:05)"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    table_name,
    record_id,
    username,
    description,
    old_data::text as old_data_summary,
    new_data::text as new_data_summary,
    TO_CHAR(created_at, 'HH24:MI:SS') as vaqt
FROM operations_history 
WHERE table_name = 'sale_items' 
  AND DATE(created_at) = CURRENT_DATE
  AND created_at BETWEEN '2026-02-08 16:00:00' AND '2026-02-08 16:05:00'
ORDER BY created_at;
"

echo ""
echo "================================"
echo "BUGUNGI BARCHA 'DELETE' OPERATSIYALARI"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    table_name,
    record_id,
    username,
    description,
    old_data::text as o_chirilgan_data,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as vaqt
FROM operations_history 
WHERE operation_type = 'DELETE'
  AND DATE(created_at) = CURRENT_DATE
ORDER BY created_at DESC
LIMIT 20;
"

echo ""
echo "================================"
echo "INVENTORIES/STOCK JADVALLARINI QIDIRISH"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
  AND (table_name LIKE '%stock%' OR table_name LIKE '%inventory%' OR table_name LIKE '%warehouse%')
ORDER BY table_name;
"
