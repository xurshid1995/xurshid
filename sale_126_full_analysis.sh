#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "========================================================================================================="
echo "                           SALE 126 CHUQUR TAHLIL - XATOLIK SABABI                                       "
echo "========================================================================================================="

echo ""
echo "=== 1. SALE 126 TO'LIQ MA'LUMOT ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as yaratildi,
    TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI:SS') as tahrirlandi,
    created_by,
    total_amount as jami_usd,
    cash_usd,
    (cash_usd - total_amount) as yo_qolgan_usd,
    cash_amount as cash_uzs,
    currency_rate as kurs,
    notes
FROM sales 
WHERE id = 126;
"

echo ""
echo "=== 2. HAQIQIY TO'LOV HISOB-KITOBI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    'To'\''lovda' as tur,
    241.00 as summa_usd,
    2964300.00 as summa_uzs,
    12300 as kurs,
    2964300.00 / 12300 as hisoblangan_usd
UNION ALL
SELECT 
    'Saqlangan' as tur,
    56.00 as summa_usd,
    56.00 * 12300 as summa_uzs,
    12300 as kurs,
    56.00 as hisoblangan_usd
UNION ALL
SELECT 
    'FARQ (yo'\''qolgan)' as tur,
    185.00 as summa_usd,
    185.00 * 12300 as summa_uzs,
    12300 as kurs,
    185.00 as hisoblangan_usd;
"

echo ""
echo "=== 3. TEYES Q8 NARXINI TEKSHIRISH ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    name,
    sell_price as sotuv_narxi_usd,
    cost_price as tan_narxi_usd,
    sell_price * 5 as agar_5_ta_sotilsa,
    barcode
FROM products 
WHERE id IN (92, 141, 144);
"

echo ""
echo "=== 4. AGAR 185 USD = 5 TA TEYES Q8 BO'LSA ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    'Agar Teyes Q8 1 ta' as holat,
    185.00 / 5 as narx_usd,
    'Demak 1 ta Teyes Q8 = 37 USD' as natija
UNION ALL
SELECT 
    'Haqiqiy umumiy summa',
    56.00 + 185.00 as narx_usd,
    '2 ta Lenova (56 USD) + 5 ta Teyes Q8 (185 USD)' as natija;
"

echo ""
echo "=== 5. SALE 126 HAMMA OPERATSIYALARNI QIDIRISH ==="
# Check if there are any audit logs or history
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
  AND (table_name LIKE '%log%' OR table_name LIKE '%audit%' OR table_name LIKE '%history%')
ORDER BY table_name;
"

echo ""
echo "=== 6. O'CHIRILGAN SALE_ITEMS BORMI? ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    MIN(id) as min_id,
    MAX(id) as max_id,
    COUNT(*) as jami_records,
    MAX(id) - MIN(id) + 1 as kutilgan_records,
    (MAX(id) - MIN(id) + 1) - COUNT(*) as yo_qolgan_records
FROM sale_items
WHERE sale_id = 126;
"

echo ""
echo "=== 7. SALE 126 ATROFIDAGI SALE_ITEMS ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    si.id,
    si.sale_id,
    si.product_id,
    p.name,
    si.quantity,
    si.total_price,
    TO_CHAR(si.created_at, 'YYYY-MM-DD HH24:MI:SS') as yaratildi
FROM sale_items si
LEFT JOIN products p ON si.product_id = p.id
WHERE si.id BETWEEN 398 AND 408
ORDER BY si.id;
"

echo ""
echo "========================================================================================================="
echo "                                         XULOSA                                                          "
echo "========================================================================================================="
echo "1. Sale 126 da faqat 1 ta element bor: 2 ta Lenova HR17 Seriy (56 USD)"
echo "2. Lekin cash_usd = 241 USD to'langan"  
echo "3. Farq: 185 USD (ehtimol 5 ta Teyes Q8)"
echo "4. Agar 185 USD / 5 = 37 USD har bir Teyes Q8 uchun"
echo "5. Savdo 'Pending' holatta va to'liq tasdiqlanmagan"
echo "========================================================================================================="
