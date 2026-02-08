#!/bin/bash

echo "SAVDO ID 115 TAFSILOTLARI:"
echo "=================================="

sudo -u postgres psql xurshid_db -c "
SELECT 
    s.id,
    s.sale_date,
    s.total_amount,
    s.cash_usd,
    s.total_profit
FROM sales s 
WHERE s.id = 115;
"

echo ""
echo "SAVDO MAHSULOTLARI:"
sudo -u postgres psql xurshid_db -c "
SELECT 
    si.product_id,
    p.name as mahsulot,
    si.quantity,
    si.price,
    (si.quantity * si.price) as jami
FROM sale_items si
LEFT JOIN products p ON si.product_id = p.id
WHERE si.sale_id = 115;
"

echo ""
echo "BU SAVDONI TUZATISH KERAKMI?"
echo "=================================="
