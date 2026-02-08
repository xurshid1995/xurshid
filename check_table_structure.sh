#!/bin/bash
export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "=== PRODUCTS JADVAL TUZILMASI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'products' ORDER BY ordinal_position;"

echo ""
echo "=== SALE_ITEMS JADVAL TUZILMASI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'sale_items' ORDER BY ordinal_position;"

echo ""
echo "=== SALE 126 ELEMENTLARI (to'liq ma'lumot) ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT * FROM sale_items WHERE sale_id = 126;"

echo ""
echo "=== TEYES MAHSULOTLARINI QIDIRISH ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT id, name, barcode FROM products WHERE LOWER(name) LIKE '%teyes%' OR LOWER(name) LIKE '%q8%' LIMIT 10;"

echo ""
echo "=== LENOVO MAHSULOTLARINI QIDIRISH ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT id, name, barcode FROM products WHERE LOWER(name) LIKE '%lenovo%' OR LOWER(name) LIKE '%lenova%' LIMIT 10;"

echo ""
echo "=== SALE 126 CASH SUMMA VA FARQ TEKSHIRUVI ==="
psql -U xurshid_user -d xurshid_db -h localhost -c "
SELECT 
    id,
    total_amount as total_usd,
    cash_usd,
    (cash_usd - total_amount) as farq_usd,
    cash_amount as cash_uzs,
    currency_rate,
    (cash_amount / currency_rate) as hisoblangan_cash_usd,
    notes
FROM sales 
WHERE id = 126;
"
