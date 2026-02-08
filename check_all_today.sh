#!/bin/bash

echo "BUGUNGI BARCHA SAVDOLAR:"
echo "=================================="

sudo -u postgres psql xurshid_db -c "
SELECT 
    s.id,
    TO_CHAR(s.sale_date, 'HH24:MI') as vaqt,
    s.total_amount,
    s.cash_usd,
    s.click_usd,
    s.terminal_usd,
    s.debt_usd,
    (s.cash_usd + s.click_usd + s.terminal_usd + s.debt_usd) as tolov_jami,
    CASE 
        WHEN ABS(s.total_amount - (s.cash_usd + s.click_usd + s.terminal_usd + s.debt_usd)) > 0.01 
        THEN 'XATO!' 
        ELSE 'OK' 
    END as status,
    (SELECT COUNT(*) FROM sale_items WHERE sale_id = s.id) as items_count,
    (SELECT SUM(total_price) FROM sale_items WHERE sale_id = s.id) as items_total
FROM sales s
WHERE DATE(s.sale_date) = CURRENT_DATE
ORDER BY s.sale_date;
"

echo ""
echo "JAMI STATISTIKA:"
echo "=================================="
sudo -u postgres psql xurshid_db -c "
SELECT 
    COUNT(*) as savdolar_soni,
    ROUND(SUM(total_amount), 2) as total_amount,
    ROUND(SUM(cash_usd), 2) as naqd,
    ROUND(SUM(click_usd), 2) as click,
    ROUND(SUM(terminal_usd), 2) as terminal,
    ROUND(SUM(debt_usd), 2) as qarz
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE;
"

echo ""
echo "SALE_ITEMS JAMI:"
echo "=================================="
sudo -u postgres psql xurshid_db -c "
SELECT 
    ROUND(SUM(si.total_price), 2) as items_total_price
FROM sale_items si
JOIN sales s ON si.sale_id = s.id
WHERE DATE(s.sale_date) = CURRENT_DATE;
"
