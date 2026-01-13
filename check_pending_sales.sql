-- Pending savdolarni ko'rish
\echo '=== PENDING SAVDOLAR ==='
SELECT id, customer_id, payment_status, payment_method, 
       to_char(sale_date, 'YYYY-MM-DD HH24:MI') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI') as created_at,
       cash_usd, click_usd, terminal_usd, debt_usd,
       substring(notes, 1, 40) as notes
FROM sales 
WHERE payment_status = 'pending' 
ORDER BY created_at DESC 
LIMIT 10;

\echo ''
\echo '=== OXIRGI 10 TA SAVDO (barcha statuslar) ==='
SELECT id, customer_id, payment_status, 
       to_char(sale_date, 'YYYY-MM-DD HH24:MI') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI') as created_at,
       cash_usd, click_usd, terminal_usd, debt_usd
FROM sales 
ORDER BY created_at DESC 
LIMIT 10;

\echo ''
\echo '=== PAID STATUSDAGI OXIRGI 5 TA SAVDO ==='
SELECT id, customer_id, payment_status,
       to_char(sale_date, 'YYYY-MM-DD HH24:MI') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI') as created_at,
       cash_usd, terminal_usd, debt_usd
FROM sales
WHERE payment_status = 'paid'
ORDER BY created_at DESC
LIMIT 5;
