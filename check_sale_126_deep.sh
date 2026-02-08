#!/bin/bash
# Sale 126 ni chuqur tekshirish

export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "================================"
echo "SALE 126 BATAFSIL MA'LUMOT"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT * FROM sales WHERE id = 126;
"

echo ""
echo "================================"
echo "SALE 126 BARCHA ELEMENTLARI"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    si.*,
    p.name as product_name,
    p.price as current_price
FROM sale_items si
LEFT JOIN products p ON si.product_id = p.id
WHERE si.sale_id = 126
ORDER BY si.id;
"

echo ""
echo "================================"
echo "TEYES Q8 MAHSULOTINI QIDIRISH"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT id, name, price, barcode 
FROM products 
WHERE LOWER(name) LIKE '%teyes%' OR LOWER(name) LIKE '%q8%'
ORDER BY id;
"

echo ""
echo "================================"
echo "BUGUNGI BARCHA SAVDOLAR"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    s.id,
    s.created_at,
    s.total_amount,
    s.payment_method,
    COUNT(si.id) as items_count
FROM sales s
LEFT JOIN sale_items si ON s.id = si.sale_id
WHERE DATE(s.created_at) = CURRENT_DATE
GROUP BY s.id
ORDER BY s.created_at DESC;
"

echo ""
echo "================================"
echo "SALE 126 OLDIDAGI VA KEYINGI SAVDOLAR"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT id, created_at, total_amount, payment_method 
FROM sales 
WHERE id >= 124 AND id <= 128
ORDER BY id;
"

echo ""
echo "================================"
echo "LENOVO MAHSULOTLARI"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT id, name, price, barcode 
FROM products 
WHERE LOWER(name) LIKE '%lenovo%' OR LOWER(name) LIKE '%lenova%'
ORDER BY id
LIMIT 10;
"
