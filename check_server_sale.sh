#!/bin/bash
# Serverdan oxirgi savdoni tekshirish

export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "================================"
echo "BUGUNGI SAVDOLAR"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT COUNT(*) as bugun_savdolar FROM sales WHERE DATE(created_at) = CURRENT_DATE;"

echo ""
echo "================================"
echo "OXIRGI SAVDO"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT id, customer_id, total_amount, payment_method, created_at FROM sales ORDER BY created_at DESC LIMIT 1;"

echo ""
echo "================================"
echo "OXIRGI SAVDO ELEMENTLARI"
echo "================================"
LAST_SALE_ID=$(psql -U xurshid_user -d xurshid_db -h localhost -t -c "SELECT id FROM sales ORDER BY created_at DESC LIMIT 1;")
echo "Sale ID: $LAST_SALE_ID"
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT si.id, si.product_id, p.name, si.quantity, si.unit_price, si.total_price FROM sale_items si LEFT JOIN products p ON si.product_id = p.id WHERE si.sale_id = $LAST_SALE_ID ORDER BY si.id;"

echo ""
echo "================================"
echo "JAMI SUMMA TEKSHIRUVI"
echo "================================"
psql -U xurshid_user -d xurshid_db -h localhost -c "SELECT 
    s.id as sale_id,
    s.total_amount as sales_jadvaldagi_summa,
    (SELECT SUM(total_price) FROM sale_items WHERE sale_id = s.id) as hisoblangan_summa,
    s.total_amount - (SELECT SUM(total_price) FROM sale_items WHERE sale_id = s.id) as farq
FROM sales s
ORDER BY s.created_at DESC 
LIMIT 1;"
