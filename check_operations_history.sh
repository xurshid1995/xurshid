#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "========================================================================================================="
echo "                         OPERATIONS HISTORY TEKSHIRUVI - SALE 126                                       "
echo "========================================================================================================="

echo ""
echo "=== 1. OPERATIONS_HISTORY JADVAL TUZILMASI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'operations_history' 
ORDER BY ordinal_position;
"

echo ""
echo "=== 2. SALE 126 UCHUN BARCHA OPERATSIYALAR ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT * FROM operations_history 
WHERE reference_type = 'sale' AND reference_id = 126
ORDER BY created_at;
"

echo ""
echo "=== 3. BUGUN SOAT 16:00-16:05 ORALIG'IDAGI BARCHA OPERATSIYALAR ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    oh.id,
    oh.operation_type,
    oh.product_id,
    p.name as mahsulot,
    oh.quantity,
    oh.reference_type,
    oh.reference_id,
    TO_CHAR(oh.created_at, 'HH24:MI:SS') as vaqt
FROM operations_history oh
LEFT JOIN products p ON oh.product_id = p.id
WHERE DATE(oh.created_at) = CURRENT_DATE 
  AND oh.created_at BETWEEN '2026-02-08 16:00:00' AND '2026-02-08 16:05:00'
ORDER BY oh.created_at;
"

echo ""
echo "=== 4. TEYES Q8 (ID: 144) MAHSULOTI OPERATSIYALARI BUGUN ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    quantity,
    reference_type,
    reference_id,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as vaqt,
    user_id,
    location_id
FROM operations_history
WHERE product_id = 144 
  AND DATE(created_at) = CURRENT_DATE
ORDER BY created_at;
"

echo ""
echo "=== 5. LENOVA HR17 (ID: 97) MAHSULOTI OPERATSIYALARI BUGUN ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    operation_type,
    quantity,
    reference_type,
    reference_id,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as vaqt,
    user_id,
    location_id
FROM operations_history
WHERE product_id = 97 
  AND DATE(created_at) = CURRENT_DATE
ORDER BY created_at;
"

echo ""
echo "=== 6. TEYES Q8 (144) HOZIRGI STOCK HOLATI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    product_id,
    location_id,
    quantity as hozirgi_miqdor,
    updated_at as oxirgi_yangilanish
FROM stock
WHERE product_id = 144;
"

echo ""
echo "=== 7. O'CHIRILGAN YOKI BEKOR QILINGAN SAVDOLAR BORMI? ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    payment_status,
    total_amount,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as yaratildi,
    notes
FROM sales
WHERE id BETWEEN 120 AND 130
  AND (payment_status != 'paid' OR notes LIKE '%Pending%' OR notes LIKE '%bekor%')
ORDER BY id;
"

echo ""
echo "========================================================================================================="
echo "                                         MUAMMO TOPISH                                                   "
echo "========================================================================================================="
