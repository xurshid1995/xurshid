#!/bin/bash

echo "SAVDO 115 NI TUZATISH:"
echo "=================================="
echo "Eski qiymat: cash_usd = 19.00"
echo "Yangi qiymat: cash_usd = 9.50"
echo ""

sudo -u postgres psql xurshid_db -c "
UPDATE sales 
SET cash_usd = 9.50 
WHERE id = 115;
"

echo ""
echo "TUZATILGAN MA'LUMOT:"
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    total_amount,
    cash_usd,
    (cash_usd + click_usd + terminal_usd + debt_usd) as tolov_jami,
    CASE 
        WHEN ABS(total_amount - (cash_usd + click_usd + terminal_usd + debt_usd)) < 0.01 
        THEN 'OK' 
        ELSE 'XATO' 
    END as status
FROM sales 
WHERE id = 115;
"

echo ""
echo "BUGUNGI YANGILANGAN STATISTIKA:"
sudo -u postgres psql xurshid_db -c "
SELECT 
    COUNT(*) as savdolar,
    ROUND(SUM(total_amount), 2) as total,
    ROUND(SUM(cash_usd), 2) as naqd
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE;
"
