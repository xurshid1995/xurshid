#!/bin/bash

echo "SAVDO 115 TARIXINI TEKSHIRISH:"
echo "=================================="

# Operations History dan bu savdoni topish
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    operation_type,
    TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS') as vaqt,
    username,
    description,
    old_data,
    new_data
FROM operations_history
WHERE table_name = 'sales' AND record_id = 115
ORDER BY timestamp;
"

echo ""
echo "SAVDONING HOZIRGI HOLATI:"
echo "=================================="
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    TO_CHAR(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date,
    customer_id,
    seller_id,
    total_amount,
    cash_usd,
    click_usd,
    terminal_usd,
    debt_usd,
    payment_status,
    payment_method,
    created_by
FROM sales
WHERE id = 115;
"

echo ""
echo "SAVDONING MAHSULOTLARI:"
echo "=================================="
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    product_id,
    quantity,
    unit_price,
    total_price,
    notes
FROM sale_items
WHERE sale_id = 115;
"
