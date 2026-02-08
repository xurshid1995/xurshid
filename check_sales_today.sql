-- Bugungi savdolarni tekshirish
SELECT 
    id,
    TO_CHAR(sale_date, 'HH24:MI') as time,
    total_amount,
    cash_usd,
    click_usd,
    terminal_usd,
    debt_usd,
    ROUND(cash_usd + click_usd + terminal_usd + debt_usd, 2) as payment_sum,
    CASE 
        WHEN ROUND(total_amount, 2) != ROUND(cash_usd + click_usd + terminal_usd + debt_usd, 2) 
        THEN 'XATO!' 
        ELSE 'OK' 
    END as status
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE 
ORDER BY sale_date DESC 
LIMIT 15;

-- Jami statistika
SELECT 
    COUNT(*) as total_sales,
    ROUND(SUM(total_amount), 2) as total_sum,
    ROUND(SUM(cash_usd), 2) as cash_sum,
    ROUND(SUM(click_usd), 2) as click_sum,
    ROUND(SUM(terminal_usd), 2) as terminal_sum,
    ROUND(SUM(debt_usd), 2) as debt_sum,
    ROUND(SUM(cash_usd + click_usd + terminal_usd + debt_usd), 2) as payment_total
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE;
